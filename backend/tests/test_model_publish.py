import hashlib
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient
from test_training import RESULTS, create_run, ultralytics_layout, upload_dataset

from cv_platform.dependencies import get_container
from cv_platform.modules.instances.domain.models import ModelFile, RuntimeRequest
from cv_platform.modules.instances.infrastructure.http_gateway import HttpAlgorithmManagerGateway
from cv_platform.modules.training.publish import next_version


@pytest.mark.parametrize("size", [32, 256, 1536, 2048])
def test_published_manifest_supports_all_training_sizes(size: int) -> None:
    from cv_platform.modules.training.publish import inference_manifest

    manifest = inference_manifest("size-test", "模型", "1.0.0", "", size)
    assert manifest.resolve_parameters({})["image_size"] == size


def finished_run(client: TestClient, node, *, files=None, status="completed") -> dict:
    dataset = upload_dataset(client, ultralytics_layout())
    run = create_run(client, dataset["id"], parameters={"epochs": 3, "imgsz": 960})
    orchestrator = get_container().training_orchestrator
    orchestrator.prepare(UUID(run["id"]))
    node.state = {
        "state": status, "progress": None, "metrics": RESULTS,
        "files": files or {"weights/best.pt": 1, "weights/last.pt": 1, "results.csv": 1},
    }
    orchestrator.sync(UUID(run["id"]))
    return run


def publish(client: TestClient, run_id: str, **payload):
    return client.post(f"/api/v1/training/runs/{run_id}/publish", json=payload)


def test_best_checkpoint_becomes_an_available_model_with_lineage(client: TestClient, node) -> None:
    run = finished_run(client, node)
    response = publish(client, run["id"], key="helmet-detector", name="安全帽检测")
    assert response.status_code == 201, response.text
    model = response.json()
    assert model["key"] == "helmet-detector"
    assert model["version"] == "1.0.0"
    assert model["status"] == "available"
    assert model["image"] == "cv-platform/yolo:dev"
    assert model["parameters"]["image_size"]["default"] == 960
    lineage = model["lineage"]
    assert lineage["training_run_id"] == run["id"]
    assert lineage["dataset_name"] == "猫狗"
    assert lineage["class_names"] == ["cat", "dog"]
    assert lineage["checkpoint"] == "best"
    # best.pt 对应 mAP50-95 最高的第 2 轮
    assert lineage["epoch"] == 2
    assert lineage["metrics"]["map50_95"] == 0.38

    container = get_container()
    stored = container.model_store.directory(UUID(model["id"]))
    weights = (stored / "model.pt").read_bytes()
    assert weights == b"content of weights/best.pt"
    assert lineage["files"]["model.pt"]["sha256"] == hashlib.sha256(weights).hexdigest()
    manifest = yaml.safe_load((stored / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["id"] == "helmet-detector" and manifest["version"] == "1.0.0"

    algorithm = container.algorithms.get(UUID(model["id"]))
    assert algorithm.model_directory == f"trained/{UUID(model['id']).hex}"
    request = container.deployment_orchestrator._instances.runtime_request(algorithm.id)
    assert request.model_directory == algorithm.model_directory
    assert {item.name for item in request.model_files} == {"model.pt", "manifest.yaml"}

    last = publish(client, run["id"], key="helmet-detector", checkpoint="last").json()
    assert last["version"] == "1.0.1"
    assert last["lineage"]["epoch"] == 3
    assert last["name"] == "helmet-detector"


def test_publishing_is_validated(client: TestClient, node) -> None:
    run = finished_run(client, node, files={"weights/last.pt": 1}, status="stopped")
    assert publish(client, run["id"], key="helmet").json()["code"] == "CHECKPOINT_MISSING"
    assert publish(client, run["id"], key="helmet", checkpoint="last").status_code == 201
    duplicate = publish(client, run["id"], key="helmet", checkpoint="last", version="1.0.0")
    assert duplicate.json()["code"] == "ALGORITHM_VERSION_EXISTS"
    taken = publish(client, run["id"], key="faster-rcnn-resnet50", checkpoint="last")
    assert taken.json()["code"] == "MODEL_KEY_TAKEN"
    bad_version = publish(client, run["id"], key="helmet", checkpoint="last", version="v2")
    assert bad_version.status_code == 400
    bad_key = publish(client, run["id"], key="Helmet Detector", checkpoint="last")
    assert bad_key.status_code == 400

    dataset = upload_dataset(client, ultralytics_layout(), name="备用")
    queued = create_run(client, dataset["id"])
    assert publish(client, queued["id"], key="helmet").json()["code"] == "TRAINING_NOT_FINISHED"


def test_deleting_a_published_version_removes_its_stored_weights(client: TestClient, node) -> None:
    run = finished_run(client, node)
    model = publish(client, run["id"], key="helmet-detector").json()
    stored = get_container().model_store.directory(UUID(model["id"]))
    assert stored.is_dir()
    assert client.delete(f"/api/v1/algorithm-versions/{model['id']}").status_code == 204
    assert not stored.exists()


def test_version_numbers_continue_from_the_highest() -> None:
    assert next_version([]) == "1.0.0"
    assert next_version(["1.0.0", "1.2.9", "1.10.0", "2.0.0-rc.1"]) == "1.10.1"


class Uploads:
    node_id = "dell"
    node_name = "Dell"

    def __init__(self, present):
        self.present = present
        self.uploaded = []

    def request(self, method, path, **_):
        assert path == "/models/trained/abc"
        return {"files": self.present}

    def upload(self, path, source, *, headers, progress=None):
        self.uploaded.append((path, source, headers["X-Content-SHA256"]))


def test_only_missing_or_changed_model_files_are_uploaded(monkeypatch, tmp_path: Path) -> None:
    cluster = get_container().algorithm_manager
    uploads = Uploads({"model.pt": {"sha256": "a" * 64}, "manifest.yaml": {"sha256": "old"}})
    monkeypatch.setattr(cluster, "node_client", lambda node_id: uploads)
    request = RuntimeRequest(
        algorithm_version_id="v", algorithm_key="helmet", image="cv-platform/yolo:dev",
        container_name="cv-algorithm-x", internal_port=8000, device="gpu", memory_mb=2048,
        model_directory="trained/abc",
        model_files=(
            ModelFile("model.pt", tmp_path / "model.pt", "a" * 64, 10),
            ModelFile("manifest.yaml", tmp_path / "manifest.yaml", "b" * 64, 5),
        ),
    )
    assert cluster.sync_model_files("dell", request) == 1
    assert uploads.uploaded == [
        ("/models/trained/abc/files/manifest.yaml", tmp_path / "manifest.yaml", "b" * 64)
    ]
    plain = RuntimeRequest(
        algorithm_version_id="v", algorithm_key="faster-rcnn", image="cv-platform/x:dev",
        container_name="cv-algorithm-y", internal_port=8000, device="gpu", memory_mb=2048,
    )
    assert cluster.sync_model_files("dell", plain) == 0


def test_the_manager_is_told_which_model_directory_to_mount(monkeypatch) -> None:
    sent = {}

    def fake_request(self, method, path, *, json=None, expect_json=True):
        sent.update(json)
        return {
            "id": "c1", "algorithm_version_id": "v", "image": "i", "container_name": "n",
            "endpoint": "http://n:8000", "status": "healthy", "device": "gpu",
            "created_at": "2026-10-23T00:00:00+00:00", "updated_at": "2026-10-23T00:00:00+00:00",
        }

    monkeypatch.setattr(HttpAlgorithmManagerGateway, "_request", fake_request)
    gateway = HttpAlgorithmManagerGateway("http://manager/api/v1")
    base = dict(algorithm_version_id="v", algorithm_key="helmet", image="cv-platform/yolo:dev",
                container_name="cv-algorithm-x", internal_port=8000, device="gpu", memory_mb=2048)
    gateway.ensure_running(RuntimeRequest(**base, model_directory="trained/abc"))
    assert sent["model_directory"] == "trained/abc"
    sent.clear()
    gateway.ensure_running(RuntimeRequest(**base))
    # 预置算法不传这个字段，旧版 Manager 也能照常工作
    assert "model_directory" not in sent
