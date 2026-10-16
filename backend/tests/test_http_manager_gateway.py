from datetime import UTC, datetime

from cv_platform.modules.instances.infrastructure.http_gateway import (
    HttpAlgorithmManagerGateway,
)


def test_manager_payload_uses_gateway_node_id() -> None:
    now = datetime.now(UTC)
    gateway = HttpAlgorithmManagerGateway("http://manager", node_id="gpu-node-1")

    instance = gateway._to_instance(
        {
            "id": "container-1",
            "algorithm_version_id": "version-1",
            "image": "cv-platform/example:dev",
            "container_name": "cv-algorithm-example",
            "endpoint": "http://cv-algorithm-example:8000",
            "status": "healthy",
            "device": "gpu",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "error": None,
            "last_used_at": None,
            "gpu_device_ids": ["0"],
        }
    )

    assert instance.node_id == "gpu-node-1"
    assert instance.gpu_device_ids == ("0",)
    assert instance.endpoint == "http://manager/instances/container-1"
