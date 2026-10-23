from dataclasses import replace
from types import SimpleNamespace

import pytest
from cv_algorithm_sdk.runtime_profile import PROTOCOL
from test_instance_service import FakeRuntime, build_spec

from algorithm_manager.application.errors import ManagerError
from algorithm_manager.application.service import InstanceService
from algorithm_manager.infrastructure.docker_runtime import DockerContainerRuntime
from algorithm_manager.infrastructure.memory_repository import InMemoryInstanceRepository


def test_deployments_are_isolated_from_each_other_and_autoscaling():
    runtime = FakeRuntime()
    service = InstanceService(InMemoryInstanceRepository(), runtime)
    spec = build_spec()
    first = service.ensure_running(replace(spec, runtime_scope="a" * 32,
                                          container_name=spec.container_name + "-da"))
    second = service.ensure_running(replace(spec, runtime_scope="b" * 32,
                                           container_name=spec.container_name + "-db"))
    automatic = service.ensure_running(spec)
    assert len({first.id, second.id, automatic.id}) == 3
    service.ensure_replicas(spec, 0)
    assert runtime.removed == [automatic.id]
    with pytest.raises(ManagerError, match="部署配置已变化"):
        service.ensure_running(replace(spec, runtime_scope="a" * 32,
                                       container_name=spec.container_name + "-da", memory_mb=8192))


def test_docker_applies_resources_and_preserves_gpu_environment(tmp_path):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="test")

    client = SimpleNamespace(containers=SimpleNamespace(create=create), images=SimpleNamespace(
        get=lambda _: SimpleNamespace(labels={"cv.platform.runtime-profile": PROTOCOL})))
    runtime = DockerContainerRuntime(client, "test", tmp_path, tmp_path, gpu_access_mode="runtime")
    spec = replace(build_spec(), memory_mb=2048, cpu_count=2.5, shm_mb=128,
                   runtime_scope="a" * 32, gpu_device_ids=("1",),
                   prediction_timeout_seconds=19, runtime_config={"threads": 3})
    runtime.create(spec)
    assert captured["nano_cpus"] == 2500000000 and captured["mem_limit"] == "2048m"
    assert captured["shm_size"] == "128m"
    assert captured["environment"]["NVIDIA_VISIBLE_DEVICES"] == "1"
    assert '"threads":3' in captured["environment"]["CV_RUNTIME_PROFILE"]
    assert captured["labels"]["cv.platform.runtime-scope"] == "a" * 32
    assert captured["labels"]["cv.platform.prediction-timeout"] == "19"


def test_old_images_cannot_silently_ignore_runtime_configuration(tmp_path):
    client = SimpleNamespace(images=SimpleNamespace(get=lambda _: SimpleNamespace(labels={})))
    runtime = DockerContainerRuntime(client, "test", tmp_path, tmp_path)
    with pytest.raises(ValueError, match="镜像不支持"):
        runtime.create(replace(build_spec(), device="cpu", runtime_config={"threads": 2}))


def test_failed_deployment_start_removes_its_container():
    class UnreadyRuntime(FakeRuntime):
        def is_healthy(self, _):
            return False

    runtime = UnreadyRuntime()
    repository = InMemoryInstanceRepository()
    service = InstanceService(repository, runtime)
    with pytest.raises(ManagerError):
        service.ensure_running(replace(build_spec(), runtime_scope="a" * 32))
    assert runtime.removed == ["container-1"] and repository.list() == []
