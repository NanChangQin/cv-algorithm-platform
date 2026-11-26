from uuid import UUID

import yaml
from cv_algorithm_sdk.training_contract import FASTER_CONTRACT, FASTER_MODEL
from test_training import RESULTS, ultralytics_layout, upload_dataset

from cv_platform.dependencies import get_container
from cv_platform.modules.training.domain import TrainingStatus


def test_faster_cpu_contract_publish_and_reuse(client, node):
    dataset = upload_dataset(client, ultralytics_layout())
    contract = client.get(
        "/api/v1/training/parameter-contract", params={"model": FASTER_MODEL}
    ).json()
    assert contract["id"] == FASTER_CONTRACT and "mosaic" not in contract["parameters"]
    body = {
        "model": FASTER_MODEL,
        "architecture": FASTER_MODEL,
        "dataset_id": dataset["id"],
        "node_id": "local",
        "parameters": {"device": "cpu", "epochs": 1, "imgsz": 128, "max_size": 192},
    }
    result = client.post("/api/v1/training/runs", json=body)
    assert result.status_code == 201, result.text
    run = result.json()
    assert run["gpu_device_ids"] == [] and run["parameters"]["optimizer"] == "SGD"
    bad = client.post(
        "/api/v1/training/runs", json={**body, "parameters": {"device": "cpu", "mosaic": 1}}
    )
    assert bad.status_code == 422
    bad = client.post("/api/v1/training/runs", json={**body, "gpu_device_ids": ["0"]})
    assert bad.status_code == 422
    bad = client.post(
        "/api/v1/training/runs",
        json={**body, "parameters": {"device": "cpu", "imgsz": 600, "max_size": 400}},
    )
    assert bad.status_code == 422
    container = get_container()
    key = UUID(run["id"])
    directory = container.training_storage.run_directory(key)
    (directory / "weights").mkdir(parents=True)
    (directory / "weights/best.pt").write_bytes(b"opaque-test-only")
    container.training.update(
        key,
        {TrainingStatus.QUEUED},
        status=TrainingStatus.COMPLETED,
        metrics=tuple(RESULTS),
        artifacts={"weights/best.pt": 16},
    )
    response = client.post(
        f"/api/v1/training/runs/{key}/publish",
        json={
            "key": "faster-pets",
            "name": "Faster R-CNN 猫狗",
            "checkpoint": "best",
            "version": "1.0.0",
        },
    )
    assert response.status_code == 201, response.text
    model = response.json()
    assert model["image"] == "cv-platform/faster-rcnn:dev" and model["device"] == "auto"
    assert "nms_threshold" in model["parameters"] and "image_size" not in model["parameters"]
    files = model["lineage"]["files"]
    assert set(files) == {"model.pth", "model-config.yaml", "manifest.yaml"}
    cfg = yaml.safe_load(
        (container.model_store.directory(UUID(model["id"])) / "model-config.yaml").read_text()
    )
    assert cfg["_dataproc"]["category_map"] == {"cat": 1, "dog": 2}
    assert (
        cfg["model"]["num_classes"] == 3
        and cfg["model"]["min_size"] == 128
        and cfg["model"]["max_size"] == 192
    )
    source = {"kind": "algorithm", "reference": model["id"]}
    response = client.post("/api/v1/training/runs", json={**body, "source": source})
    assert response.status_code == 201, response.text
    assert response.json()["progress"]["architecture"] == FASTER_MODEL
    inferred = {k: v for k, v in body.items() if k not in {"model", "architecture"}}
    response = client.post("/api/v1/training/runs", json={**inferred, "source": source})
    assert response.status_code == 201, response.text
    assert response.json()["model"] == FASTER_MODEL
    response = client.post(
        "/api/v1/training/runs",
        json={
            **body,
            "model": "yolo11n.pt",
            "architecture": "yolo11n.pt",
            "source": source,
            "parameters": {},
        },
    )
    assert response.status_code == 422, response.text
    assert (
        client.get(
            "/api/v1/training/parameter-contract", params={"model": FASTER_MODEL, "mode": "export"}
        ).status_code
        == 422
    )
