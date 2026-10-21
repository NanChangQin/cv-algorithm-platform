import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from cv_platform.dependencies import get_container
from cv_platform.modules.scheduling.cluster_gateway import RuntimeNode


@pytest.fixture
def asset_id(client: TestClient) -> str:
    image = io.BytesIO()
    Image.new("RGB", (16, 12), "red").save(image, format="PNG")
    files = [("files", ("demo.png", image.getvalue(), "image/png"))]
    response = client.post("/api/v1/assets/upload", files=files)
    assert response.status_code == 201
    return response.json()["assets"][0]["id"]


def test_create_task_applies_default_algorithm_parameters(
    client: TestClient, asset_id: str
) -> None:
    algorithm = client.get("/api/v1/algorithms").json()[0]
    response = client.post(
        "/api/v1/tasks",
        json={
            "algorithm_version_id": algorithm["id"],
            "asset_id": asset_id,
            "parameters": {},
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    assert response.json()["parameters"]["confidence"] == 0.5
    assert response.json()["parameters"]["nms_threshold"] == 0.5
    assert response.json()["parameters"]["max_detections"] == 100
    assert response.json()["parameters"]["min_box_area"] == 0


def test_create_task_rejects_unknown_parameter(client: TestClient, asset_id: str) -> None:
    algorithm = client.get("/api/v1/algorithms").json()[0]
    response = client.post(
        "/api/v1/tasks",
        json={
            "algorithm_version_id": algorithm["id"],
            "asset_id": asset_id,
            "parameters": {"unknown": True},
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "PARAMETER_INVALID"


def test_create_task_rejects_unmanaged_asset_uri(client: TestClient) -> None:
    algorithm = client.get("/api/v1/algorithms").json()[0]
    response = client.post(
        "/api/v1/tasks",
        json={
            "algorithm_version_id": algorithm["id"],
            "asset_uri": "file:///etc/passwd",
            "parameters": {},
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INPUT_INVALID"


def test_cancel_and_retry_task(client: TestClient, asset_id: str) -> None:
    algorithm = client.get("/api/v1/algorithms").json()[0]
    created = client.post(
        "/api/v1/tasks",
        json={
            "algorithm_version_id": algorithm["id"],
            "asset_id": asset_id,
            "parameters": {},
        },
    ).json()

    cancelled = client.post(f"/api/v1/tasks/{created['id']}/cancel")
    retried = client.post(f"/api/v1/tasks/{created['id']}/retry")

    assert cancelled.json()["status"] == "cancelled"
    assert retried.status_code == 201
    assert retried.json()["retry_of"] == created["id"]


def test_requested_placement_is_preserved_on_retry(client: TestClient, asset_id: str) -> None:
    container = get_container()
    container.algorithm_manager.register_node(RuntimeNode("remote", "Remote GPU", "http://remote"))
    algorithm = client.get("/api/v1/algorithms").json()[0]
    created = client.post("/api/v1/tasks", json={
        "algorithm_version_id": algorithm["id"], "asset_id": asset_id,
        "node_id": "remote", "gpu_device_ids": ["0"],
    })
    assert created.status_code == 201
    task = created.json()
    assert task["node_id"] == "remote"
    assert task["gpu_device_ids"] == ["0"]
    client.post(f"/api/v1/tasks/{task['id']}/cancel")
    retried = client.post(f"/api/v1/tasks/{task['id']}/retry").json()
    assert retried["node_id"] == "remote"
    assert retried["gpu_device_ids"] == ["0"]


def test_unknown_node_is_rejected_before_queueing(client: TestClient, asset_id: str) -> None:
    algorithm = client.get("/api/v1/algorithms").json()[0]
    response = client.post("/api/v1/tasks", json={
        "algorithm_version_id": algorithm["id"], "asset_id": asset_id,
        "node_id": "missing",
    })
    assert response.status_code == 404
    assert response.json()["code"] == "RUNTIME_NODE_NOT_FOUND"


def test_gpu_requires_node_and_numeric_unique_ids(client: TestClient, asset_id: str) -> None:
    algorithm = client.get("/api/v1/algorithms").json()[0]
    for placement in ({"gpu_device_ids": ["0"]}, {"node_id": "local", "gpu_device_ids": ["../0"]},
                      {"node_id": "local", "gpu_device_ids": ["0", "0"]}):
        response = client.post("/api/v1/tasks", json={
            "algorithm_version_id": algorithm["id"], "asset_id": asset_id,
            **placement,
        })
        assert response.status_code == 422
