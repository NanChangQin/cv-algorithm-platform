from dataclasses import replace
from uuid import UUID

from ....core.errors import ApplicationError
from ....core.project_context import DEFAULT_PROJECT_ID
from ...algorithms.domain.repositories import AlgorithmRepository
from ..domain.gateway import AlgorithmManagerGateway
from ..domain.models import RuntimeInstance, RuntimeRequest


class RuntimeInstanceService:
    def __init__(
        self,
        algorithms: AlgorithmRepository,
        manager: AlgorithmManagerGateway,
        project_id: UUID = DEFAULT_PROJECT_ID,
    ) -> None:
        self._algorithms = algorithms
        self._manager = manager
        self._project_id = project_id

    def list_instances(self, project_id: UUID | None = None) -> list[RuntimeInstance]:
        expected_project_id = project_id or self._project_id
        algorithm_ids = {
            str(item.id)
            for item in self._algorithms.list()
            if item.project_id == expected_project_id
        }
        return [
            item for item in self._manager.list_instances()
            if item.algorithm_version_id in algorithm_ids
        ]

    def start_algorithm(
        self,
        algorithm_id: UUID,
        project_id: UUID | None = None,
        *,
        node_id: str | None = None,
        gpu_device_ids: tuple[str, ...] = (),
    ) -> RuntimeInstance:
        request = self._runtime_request(algorithm_id, project_id)
        if gpu_device_ids and request.device == "cpu":
            raise ApplicationError("PLACEMENT_INVALID", "该算法仅支持 CPU，不能指定 GPU")
        return self._manager.ensure_running(
            replace(request, node_id=node_id, gpu_device_ids=gpu_device_ids)
        )

    def ensure_replicas(
        self,
        algorithm_id: UUID,
        replicas: int,
        project_id: UUID | None = None,
    ) -> list[RuntimeInstance]:
        return self._manager.ensure_replicas(
            self._runtime_request(algorithm_id, project_id),
            replicas,
        )

    def _runtime_request(
        self,
        algorithm_id: UUID,
        project_id: UUID | None = None,
    ) -> RuntimeRequest:
        algorithm = self._algorithms.get(algorithm_id)
        expected_project_id = project_id or self._project_id
        if algorithm is None or algorithm.project_id != expected_project_id:
            raise ApplicationError("ALGORITHM_NOT_FOUND", "算法版本不存在", 404)
        manifest = algorithm.manifest
        request = RuntimeRequest(
            algorithm_version_id=str(algorithm.id),
            algorithm_key=manifest.id,
            image=algorithm.image,
            container_name=f"cv-algorithm-{algorithm.id.hex[:12]}",
            internal_port=algorithm.internal_port,
            device=manifest.runtime.device.value,
            memory_mb=manifest.runtime.min_memory_mb,
        )
        return request

    def stop(self, instance_id: str) -> RuntimeInstance:
        self._required_instance(instance_id)
        return self._manager.stop(instance_id)

    def remove(self, instance_id: str) -> None:
        self._required_instance(instance_id)
        self._manager.remove(instance_id)

    def logs(self, instance_id: str, tail: int = 200) -> list[str]:
        self._required_instance(instance_id)
        return self._manager.logs(instance_id, tail)

    def list_gpus(self) -> list[dict[str, object]]:
        return self._manager.list_gpus()

    def touch(
        self,
        instance_id: str,
        project_id: UUID | None = None,
    ) -> RuntimeInstance:
        self._required_instance(instance_id, project_id)
        return self._manager.touch(instance_id)

    def _required_instance(
        self,
        instance_id: str,
        project_id: UUID | None = None,
    ) -> RuntimeInstance:
        expected_project_id = project_id or self._project_id
        for instance in self._manager.list_instances():
            if instance.id != instance_id:
                continue
            try:
                algorithm_id = UUID(instance.algorithm_version_id)
            except ValueError:
                break
            algorithm = self._algorithms.get(algorithm_id)
            if algorithm is not None and algorithm.project_id == expected_project_id:
                return instance
            break
        raise ApplicationError("INSTANCE_NOT_FOUND", "算法实例不存在", 404)
