from dataclasses import replace
from datetime import UTC, datetime

import pytest

from cv_platform.core.errors import ApplicationError
from cv_platform.modules.instances.domain.models import RuntimeInstance, RuntimeRequest
from cv_platform.modules.scheduling.cluster_gateway import ClusterAlgorithmManagerGateway, RuntimeNode


class NodeGateway:
    def __init__(self, node_id, gpus=(0, 1), instances=()):
        self.node_id = node_id
        self.instances = list(instances)
        self.gpus = [{"index": index, "memory_total_mb": 16000} for index in gpus]
        self.requests = []

    def list_instances(self):
        return self.instances

    def list_gpus(self):
        return self.gpus

    def ensure_running(self, request):
        self.requests.append(request)
        now = datetime.now(UTC)
        return RuntimeInstance(
            f"{self.node_id}-container", request.algorithm_version_id, request.image,
            request.container_name, "http://container:8000", "healthy", request.device,
            now, now, node_id=self.node_id, gpu_device_ids=request.gpu_device_ids,
        )


def cluster(monkeypatch):
    manager = ClusterAlgorithmManagerGateway("http://local")
    manager.register_node(RuntimeNode("remote", "Remote GPU", "http://remote"))
    gateways = {node.id: NodeGateway(node.id) for node in manager.list_nodes()}
    monkeypatch.setattr(manager, "_gateway", lambda node: gateways[node.id])
    return manager, gateways


def request(**kwargs):
    return replace(RuntimeRequest("version", "example", "image:v1", "cv-algorithm-test", 8000,
                                  "auto", 1024), **kwargs)


def test_pin_never_reuses_another_node_or_card(monkeypatch):
    manager, nodes = cluster(monkeypatch)
    nodes["local"].instances = [nodes["local"].ensure_running(request(device="gpu"))]
    nodes["remote"].instances = [nodes["remote"].ensure_running(request(device="gpu", gpu_device_ids=("1",)))]
    nodes["local"].requests.clear()
    nodes["remote"].requests.clear()
    instance = manager.ensure_running(request(node_id="remote", gpu_device_ids=("0",)))
    assert instance.node_id == "remote"
    assert instance.device == "gpu"
    assert instance.gpu_device_ids == ("0",)
    assert nodes["local"].requests == []
    assert nodes["remote"].requests[0].container_name.endswith("-gpu-0")


def test_missing_gpu_fails_without_fallback(monkeypatch):
    manager, nodes = cluster(monkeypatch)
    with pytest.raises(ApplicationError) as error:
        manager.ensure_running(request(node_id="remote", gpu_device_ids=("9",)))
    assert error.value.code == "GPU_UNAVAILABLE"
    assert all(not node.requests for node in nodes.values())


def test_disabled_node_fails_without_fallback(monkeypatch):
    manager, nodes = cluster(monkeypatch)
    manager.register_node(RuntimeNode("remote", "Remote", "http://remote", False))
    with pytest.raises(ApplicationError) as error:
        manager.ensure_running(request(node_id="remote"))
    assert error.value.code == "RUNTIME_NODE_DISABLED"
    assert all(not node.requests for node in nodes.values())


def test_auto_without_gpu_uses_cpu(monkeypatch):
    manager, nodes = cluster(monkeypatch)
    nodes["remote"].gpus = []
    instance = manager.ensure_running(request(node_id="remote"))
    assert instance.device == "cpu"
    assert instance.gpu_device_ids == ()


def test_offline_pinned_node_reports_failure(monkeypatch):
    manager, nodes = cluster(monkeypatch)
    def offline():
        raise ApplicationError("OFFLINE", "offline", 503)
    monkeypatch.setattr(nodes["remote"], "list_instances", offline)
    with pytest.raises(ApplicationError) as error:
        manager.ensure_running(request(node_id="remote"))
    assert error.value.code == "RUNTIME_NODE_UNAVAILABLE"
    assert nodes["local"].requests == []
