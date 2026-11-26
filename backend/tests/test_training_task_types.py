from uuid import uuid4

import pytest
from test_training import create_run, ultralytics_layout, upload_dataset


@pytest.mark.parametrize(
    "task_type", ["classification", "segmentation", "pose_estimation", "oriented_detection"]
)
def test_unavailable_task_types_cannot_submit(client, node, task_type):
    response = client.post(
        "/api/v1/training/runs",
        json={
            "task_type": task_type,
            "dataset_id": str(uuid4()),
            "node_id": "local",
            "model": "yolo11s.pt",
        },
    )
    assert response.status_code == 422
    assert not node.started
    assert client.get("/api/v1/training/runs").json() == []


def test_old_and_new_requests_report_detection_task_type(client, node):
    dataset = upload_dataset(client, ultralytics_layout())
    legacy = create_run(client, dataset["id"], model="yolo11s.pt")
    assert legacy["task_type"] == "object_detection"
    response = client.post(
        "/api/v1/training/runs",
        json={
            "task_type": "object_detection",
            "dataset_id": dataset["id"],
            "node_id": "local",
            "model": "yolo26s.pt",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["task_type"] == "object_detection"
    assert all(
        row["task_type"] == "object_detection" for row in client.get("/api/v1/training/runs").json()
    )
