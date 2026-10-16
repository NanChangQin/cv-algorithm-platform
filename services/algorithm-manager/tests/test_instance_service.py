from algorithm_manager.application.service import InstanceService
from algorithm_manager.domain.models import AlgorithmInstance, ContainerSpec, InstanceStatus
from algorithm_manager.infrastructure.memory_repository import InMemoryInstanceRepository
from dataclasses import replace


class FakeRuntime:
    def __init__(self) -> None:
        self.created = 0
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.removed: list[str] = []

    def discover(self) -> list[AlgorithmInstance]:
        return []

    def create(self, spec: ContainerSpec) -> tuple[str, str]:
        self.created += 1
        return f"container-{self.created}", f"http://{spec.container_name}:{spec.internal_port}"

    def start(self, instance_id: str) -> None:
        self.started.append(instance_id)

    def stop(self, instance_id: str) -> None:
        self.stopped.append(instance_id)

    def remove(self, instance_id: str) -> None:
        self.removed.append(instance_id)

    def is_healthy(self, _: AlgorithmInstance) -> bool:
        return True

    def probe_health(self, _: AlgorithmInstance) -> bool:
        return True

    def list_gpus(self) -> list[dict[str, object]]:
        return []

    def logs(self, instance_id: str, tail: int = 200) -> list[str]:
        return [instance_id]


def build_spec() -> ContainerSpec:
    return ContainerSpec(
        algorithm_version_id="version-1",
        algorithm_key="faster-rcnn-resnet50",
        image="cv-platform/faster-rcnn:dev",
        container_name="cv-algorithm-version-1",
        device="gpu",
        memory_mb=4096,
        cpu_count=2,
    )


def test_ensure_running_reuses_healthy_instance() -> None:
    runtime = FakeRuntime()
    service = InstanceService(InMemoryInstanceRepository(), runtime)

    first = service.ensure_running(build_spec())
    second = service.ensure_running(build_spec())

    assert first.status is InstanceStatus.HEALTHY
    assert second.id == first.id
    assert runtime.created == 1
    assert runtime.started == [first.id]


def test_ensure_running_keeps_gpu_placement_separate() -> None:
    runtime = FakeRuntime()
    service = InstanceService(InMemoryInstanceRepository(), runtime)
    first = service.ensure_running(replace(build_spec(), gpu_device_ids=("0",)))
    second = service.ensure_running(replace(build_spec(), gpu_device_ids=("1",)))
    reused = service.ensure_running(replace(build_spec(), gpu_device_ids=("0",)))
    assert second.id != first.id
    assert reused.id == first.id
    assert runtime.created == 2


def test_stop_and_remove_instance() -> None:
    runtime = FakeRuntime()
    repository = InMemoryInstanceRepository()
    service = InstanceService(repository, runtime)
    instance = service.ensure_running(build_spec())

    stopped = service.stop(instance.id)
    service.remove(instance.id)

    assert stopped.status is InstanceStatus.STOPPED
    assert repository.get(instance.id) is None
    assert runtime.stopped == [instance.id]
    assert runtime.removed == [instance.id]


def test_ensure_replicas_scales_up_and_down() -> None:
    runtime = FakeRuntime()
    repository = InMemoryInstanceRepository()
    service = InstanceService(repository, runtime)

    replicas = service.ensure_replicas(build_spec(), 3)
    reduced = service.ensure_replicas(build_spec(), 1)

    assert len(replicas) == 3
    assert len(reduced) == 1
    assert runtime.created == 3
    assert len(runtime.removed) == 2


def test_health_monitor_stops_after_threshold() -> None:
    class FailingRuntime(FakeRuntime):
        def probe_health(self, _: AlgorithmInstance) -> bool:
            return False

    runtime = FailingRuntime()
    repository = InMemoryInstanceRepository()
    service = InstanceService(repository, runtime)
    instance = service.ensure_running(build_spec())

    assert service.monitor_health(2) == []
    assert service.monitor_health(2) == [instance.id]
    assert repository.get(instance.id).status is InstanceStatus.FAILED
