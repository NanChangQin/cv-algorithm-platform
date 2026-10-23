import json
from uuid import UUID

import pytest
from test_training import create_run, get_run, ultralytics_layout, upload_dataset

from cv_platform.dependencies import get_container


def test_independent_validation_runs_without_training_parameters(client, node):
    dataset = upload_dataset(client, ultralytics_layout())
    run = create_run(
        client,
        dataset["id"],
        operation="val",
        parameters={
            "batch": 2,
            "imgsz": [64, 96],
            "split": "val",
            "conf": 0.001,
            "iou": 0.7,
        },
    )
    assert "epochs" not in run["parameters"]
    assert run["progress"]["operation"] == "val"
    orchestrator = get_container().training_orchestrator
    orchestrator.prepare(UUID(run["id"]))
    assert node.started[-1]["operation"] == "val"
    assert node.started[-1]["parameters"]["split"] == "val"
    assert "epochs" not in node.started[-1]["parameters"]
    report = {
        "metrics": {"metrics/mAP50-95(B)": 0.42},
        "per_class": [],
        "split": "val",
        "dataset_sha256": dataset["sha256"],
    }
    original = node.download

    def download(path, target):
        if path.endswith("evaluation.json"):
            target.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(report).encode()
            target.write_bytes(content)
            return len(content)
        return original(path, target)

    node.download = download
    node.state = {
        "state": "completed",
        "progress": {"epoch": 1, "epochs": 1},
        "metrics": [{"epoch": 1, "metrics/mAP50-95(B)": 0.42}],
        "files": {"evaluation.json": 1, "results.csv": 1},
    }
    orchestrator.sync(UUID(run["id"]))
    final = get_run(client, run["id"])
    assert final["status"] == "completed" and final["progress"]["operation"] == "val"
    assert final["progress"]["dataset"]["sha256"] == dataset["sha256"]
    assert final["best"]["map50_95"] == 0.42
    response = client.get(f"/api/v1/training/runs/{run['id']}/evaluation")
    assert response.status_code == 200 and response.json() == report


@pytest.mark.parametrize(
    "parameters",
    [
        {"epochs": 3},
        {"batch": -1},
        {"batch": 0.5},
        {"dnn": True},
        {"split": "test"},
        {"split": "missing"},
        {"fraction": [0.8, 0.2, 0]},
        {"quantize": "fp8"},
        {"project": "/arbitrary"},
    ],
)
def test_validation_rejects_unsupported_and_absent_splits(client, node, parameters):
    dataset = upload_dataset(client, ultralytics_layout())
    response = client.post(
        "/api/v1/training/runs",
        json={
            "dataset_id": dataset["id"],
            "model": "yolo11n.pt",
            "node_id": "local",
            "operation": "val",
            "parameters": parameters,
        },
    )
    assert response.status_code == 422, response.text


def test_validation_contract_is_explicit_about_supported_parameters(client):
    contract = client.get("/api/v1/training/parameter-contract?mode=val").json()
    assert contract["parameters"]["split"]["status"] == "configurable"
    assert contract["parameters"]["data"]["status"] == "managed"
    assert contract["parameters"]["dnn"]["status"] == "unavailable"
    assert "epochs" not in contract["parameters"]
