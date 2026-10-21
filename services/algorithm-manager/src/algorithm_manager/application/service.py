from collections import defaultdict
from contextlib import suppress
from datetime import UTC, datetime
from threading import Lock
from time import monotonic

from ..domain.models import AlgorithmInstance, ContainerSpec, InstanceStatus
from ..domain.ports import ContainerRuntime, InstanceRepository
from .errors import ManagerError


class InstanceService:
    def __init__(self, repository: InstanceRepository, runtime: ContainerRuntime) -> None:
        self._repository = repository
        self._runtime = runtime
        self._locks: defaultdict[str, Lock] = defaultdict(Lock)
        self._health_failures: defaultdict[str, int] = defaultdict(int)
        self._gpu_cache: tuple[float, list[dict[str, object]]] = (0, [])
        self._gpu_lock = Lock()
        for instance in self._runtime.discover():
            self._repository.save(instance)

    def list_instances(self) -> list[AlgorithmInstance]:
        return self._repository.list()

    def ensure_running(self, spec: ContainerSpec) -> AlgorithmInstance:
        with self._locks[spec.algorithm_version_id]:
            current = min(
                (
                    item
                    for item in self._repository.find_all_by_algorithm(
                        spec.algorithm_version_id
                    )
                    if item.image == spec.image
                    and (spec.device == "auto" or item.device == spec.device)
                    and (
                        not spec.gpu_device_ids
                        or set(item.gpu_device_ids) == set(spec.gpu_device_ids)
                    )
                ),
                key=lambda item: (
                    item.status is not InstanceStatus.HEALTHY,
                    item.last_used_at or item.updated_at,
                ),
                default=None,
            )
            if current is not None and current.status is InstanceStatus.HEALTHY:
                try:
                    healthy = self._runtime.is_healthy(current)
                except Exception:
                    self._repository.delete(current.id)
                    current = None
                    healthy = False
                if current is not None and healthy:
                    current = current.touch()
                    self._repository.save(current)
                    return current
                if current is not None:
                    current = current.transition(
                        InstanceStatus.FAILED,
                        "container health check failed",
                    )
                    self._repository.save(current)

            if current is None:
                try:
                    instance_id, endpoint = self._runtime.create(spec)
                    current = AlgorithmInstance.created(instance_id, spec, endpoint)
                    self._repository.save(current)
                except Exception as exc:
                    raise ManagerError("CONTAINER_CREATE_FAILED", str(exc), 502) from exc

            current = current.transition(InstanceStatus.STARTING)
            self._repository.save(current)
            try:
                self._runtime.start(current.id)
                if not self._runtime.is_healthy(current):
                    raise RuntimeError("algorithm container did not become healthy")
            except Exception as exc:
                failed = current.transition(InstanceStatus.FAILED, str(exc))
                self._repository.save(failed)
                raise ManagerError("CONTAINER_START_FAILED", str(exc), 502) from exc

            healthy = current.transition(InstanceStatus.HEALTHY)
            self._repository.save(healthy)
            return healthy

    def ensure_replicas(self, spec: ContainerSpec, replicas: int) -> list[AlgorithmInstance]:
        if not 0 <= replicas <= 100:
            raise ManagerError("REPLICA_COUNT_INVALID", "副本数必须在 0 到 100 之间")
        with self._locks[spec.algorithm_version_id]:
            current = self._repository.find_all_by_algorithm(spec.algorithm_version_id)
            healthy = []
            for item in current:
                if item.status is not InstanceStatus.HEALTHY:
                    continue
                try:
                    if self._runtime.probe_health(item):
                        healthy.append(item)
                    else:
                        self._repository.save(
                            item.transition(InstanceStatus.FAILED, "replica health check failed")
                        )
                except Exception:
                    self._repository.delete(item.id)
            while len(healthy) < replicas:
                replica_index = 1
                existing_names = {item.container_name for item in current}
                while f"{spec.container_name}-r{replica_index}" in existing_names:
                    replica_index += 1
                replica_spec = ContainerSpec(
                    algorithm_version_id=spec.algorithm_version_id,
                    algorithm_key=spec.algorithm_key,
                    image=spec.image,
                    container_name=f"{spec.container_name}-r{replica_index}",
                    internal_port=spec.internal_port,
                    device=spec.device,
                    memory_mb=spec.memory_mb,
                    cpu_count=spec.cpu_count,
                    gpu_device_ids=(
                        (spec.gpu_device_ids[(replica_index - 1) % len(spec.gpu_device_ids)],)
                        if spec.gpu_device_ids
                        else ()
                    ),
                )
                instance: AlgorithmInstance | None = None
                try:
                    instance_id, endpoint = self._runtime.create(replica_spec)
                    instance = AlgorithmInstance.created(instance_id, replica_spec, endpoint)
                    self._repository.save(instance)
                    self._runtime.start(instance.id)
                    if not self._runtime.is_healthy(instance):
                        raise RuntimeError("replica did not become healthy")
                    instance = instance.transition(InstanceStatus.HEALTHY)
                    self._repository.save(instance)
                    current.append(instance)
                    healthy.append(instance)
                except Exception as exc:
                    if instance is not None:
                        with suppress(Exception):
                            self._runtime.remove(instance.id)
                        self._repository.delete(instance.id)
                    raise ManagerError("REPLICA_START_FAILED", str(exc), 502) from exc
            removable = sorted(
                healthy[replicas:],
                key=lambda item: item.last_used_at or item.updated_at,
            )
            for instance in removable:
                self.remove(instance.id)
                healthy.remove(instance)
            return healthy

    def reclaim_idle(self, idle_seconds: int) -> list[str]:
        stopped = []
        now = datetime.now(UTC)
        for instance in self._repository.list():
            last_used = instance.last_used_at or instance.updated_at
            if (
                instance.status is InstanceStatus.HEALTHY
                and (now - last_used).total_seconds() >= idle_seconds
            ):
                self.stop(instance.id)
                stopped.append(instance.id)
        return stopped

    def monitor_health(self, failure_threshold: int) -> list[str]:
        failed_ids = []
        for instance in self._repository.list():
            if instance.status is not InstanceStatus.HEALTHY:
                self._health_failures.pop(instance.id, None)
                continue
            try:
                healthy = self._runtime.probe_health(instance)
            except Exception:
                healthy = False
            if healthy:
                self._health_failures.pop(instance.id, None)
                continue
            self._health_failures[instance.id] += 1
            if self._health_failures[instance.id] < failure_threshold:
                continue
            with suppress(Exception):
                self._runtime.stop(instance.id)
            self._repository.save(
                instance.transition(
                    InstanceStatus.FAILED,
                    f"health check failed {failure_threshold} consecutive times",
                )
            )
            failed_ids.append(instance.id)
            self._health_failures.pop(instance.id, None)
        return failed_ids

    def stop(self, instance_id: str) -> AlgorithmInstance:
        instance = self._required(instance_id)
        try:
            self._runtime.stop(instance.id)
        except Exception as exc:
            raise ManagerError("CONTAINER_STOP_FAILED", str(exc), 502) from exc
        stopped = instance.transition(InstanceStatus.STOPPED)
        self._repository.save(stopped)
        return stopped

    def remove(self, instance_id: str) -> None:
        instance = self._required(instance_id)
        try:
            self._runtime.remove(instance.id)
        except Exception as exc:
            raise ManagerError("CONTAINER_REMOVE_FAILED", str(exc), 502) from exc
        self._repository.delete(instance.id)
        self._health_failures.pop(instance.id, None)

    def logs(self, instance_id: str, tail: int = 200) -> list[str]:
        instance = self._required(instance_id)
        try:
            return self._runtime.logs(instance.id, tail)
        except Exception as exc:
            raise ManagerError("CONTAINER_LOGS_FAILED", "容器日志读取失败", 502) from exc

    def touch(self, instance_id: str) -> AlgorithmInstance:
        instance = self._required(instance_id).touch()
        self._repository.save(instance)
        return instance

    def get_instance(self, instance_id: str) -> AlgorithmInstance:
        return self._required(instance_id)

    def list_gpus(self) -> list[dict[str, object]]:
        with self._gpu_lock:
            cached_at, cached = self._gpu_cache
            if monotonic() - cached_at < 5:
                return [dict(item) for item in cached]
            fresh = self._runtime.list_gpus()
            self._gpu_cache = (monotonic(), fresh)
            return [dict(item) for item in fresh]

    def _required(self, instance_id: str) -> AlgorithmInstance:
        instance = self._repository.get(instance_id)
        if instance is None:
            raise ManagerError("INSTANCE_NOT_FOUND", "算法实例不存在", 404)
        return instance
