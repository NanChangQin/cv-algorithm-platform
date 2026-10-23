import hashlib
import io
from dataclasses import replace
from uuid import UUID

import pytest
from cv_algorithm_sdk.training_sources import TRAINING_PROTOCOL
from test_model_publish import finished_run, publish
from test_training import create_run, get_run, ultralytics_layout, upload_dataset

from cv_platform.dependencies import get_container
from cv_platform.modules.training.domain import TrainingStatus
from cv_platform.modules.training.weights import WeightStorage


def upload_weight(client, content=b"opaque-pickle-not-loaded-by-backend", filename="custom.pt"):
    return client.post("/api/v1/training/weights", files={"file": (filename, content)})


def test_upload_is_opaque_hashed_and_frozen_before_origin_deletion(client, node):
    dataset = upload_dataset(client, ultralytics_layout())
    weight = upload_weight(client).json()
    assert weight["validation"] == "pending_runtime"
    assert weight["sha256"] == hashlib.sha256(b"opaque-pickle-not-loaded-by-backend").hexdigest()
    run = create_run(client, dataset["id"], source={"kind": "upload", "reference": weight["id"]})
    assert run["model"] == "custom.pt"
    assert run["progress"]["source"]["sha256"] == weight["sha256"]
    assert client.delete(f"/api/v1/training/weights/{weight['id']}").status_code == 204
    get_container().training_orchestrator.prepare(UUID(run["id"]))
    assert get_run(client, run["id"])["status"] == "running"
    assert node.started[-1]["training_input"] == {
        "sha256": weight["sha256"],
        "dataset_sha256": dataset["sha256"],
        "resume": False,
        "epoch": 0,
        "epochs": 0,
    }
    assert node.weights == {weight["sha256"]: weight["sha256"]}


@pytest.mark.parametrize(
    ("filename", "content", "status"),
    [
        ("model.onnx", b"a", 422),
        ("empty.pt", b"", 422),
    ],
)
def test_weight_input_validation(client, filename, content, status):
    assert upload_weight(client, content, filename).status_code == status


def test_weight_size_limit_removes_partial_upload(tmp_path, monkeypatch):
    from cv_platform.modules.training import weights

    monkeypatch.setattr(weights, "MAX_WEIGHT_BYTES", 2)
    store = WeightStorage(tmp_path, UUID(int=1))
    with pytest.raises(Exception, match="2 GiB"):
        store.upload(io.BytesIO(b"123"), "x.pt", "admin")
    assert list(tmp_path.rglob("model.pt")) == []


def test_sources_are_project_scoped(client, node):
    dataset = upload_dataset(client, ultralytics_layout())
    weight = upload_weight(client).json()
    project = client.post("/api/v1/projects", json={"name": "other"}).json()
    container = get_container()
    foreign = WeightStorage(container.training_storage.root, UUID(project["id"]))
    foreign_weight = foreign.upload(io.BytesIO(b"other"), "other.pt", "admin")
    options = client.get("/api/v1/training/sources").json()
    references = [item["source"]["reference"] for item in options]
    assert weight["id"] in references and foreign_weight["id"] not in references
    response = client.post(
        "/api/v1/training/runs",
        json={
            "dataset_id": dataset["id"],
            "node_id": "local",
            "source": {"kind": "upload", "reference": foreign_weight["id"]},
        },
    )
    assert response.status_code == 404
    assert client.delete(f"/api/v1/training/weights/{foreign_weight['id']}").status_code == 404


def test_published_model_finetune_has_immutable_lineage(client, node):
    parent = finished_run(client, node)
    model = publish(client, parent["id"], key="source-model").json()
    run = create_run(
        client, parent["dataset_id"], source={"kind": "algorithm", "reference": model["id"]}
    )
    container = get_container()
    assert run["progress"]["source"]["sha256"] == model["lineage"]["files"]["model.pt"]["sha256"]
    container.model_store.delete(UUID(model["id"]))
    container.algorithms.delete(UUID(model["id"]))
    container.training_orchestrator.prepare(UUID(run["id"]))
    assert get_run(client, run["id"])["status"] == "running"


def resumable_parent(client):
    dataset = upload_dataset(client, ultralytics_layout())
    run = create_run(client, dataset["id"], parameters={"epochs": 5, "optimizer": "SGD"})
    container = get_container()
    path = container.training_storage.run_directory(run["id"]) / "weights/resume-000002.pt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"complete-training-state")
    checkpoint = {
        "protocol": TRAINING_PROTOCOL,
        "resumable": True,
        "reason": None,
        "file": "weights/resume-000002.pt",
        "dataset_sha256": dataset["sha256"],
        "epoch": 2,
        "epochs": 5,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    container.training.update(
        UUID(run["id"]),
        status=TrainingStatus.STOPPED,
        artifacts={checkpoint["file"]: path.stat().st_size},
        progress={**run["progress"], "checkpoint": checkpoint},
    )
    return run, dataset, checkpoint


def test_resume_locks_plan_and_survives_parent_removal(client, node):
    parent, dataset, checkpoint = resumable_parent(client)
    container = get_container()
    best = container.training_storage.run_directory(parent["id"]) / "weights/best.pt"
    best.write_bytes(b"original-best")
    container.training.update(
        UUID(parent["id"]),
        artifacts={
            checkpoint["file"]: 1,
            "weights/best.pt": best.stat().st_size,
        },
    )
    run = create_run(
        client,
        dataset["id"],
        parameters={"batch": 2},
        source={
            "kind": "run",
            "reference": parent["id"],
            "checkpoint": "resume",
            "mode": "resume",
        },
    )
    assert run["parameters"]["epochs"] == 5 and run["parameters"]["optimizer"] == "SGD"
    assert client.delete(f"/api/v1/training/runs/{parent['id']}").status_code == 204
    get_container().training_orchestrator.prepare(UUID(run["id"]))
    assert get_run(client, run["id"])["status"] == "running"
    assert node.started[-1]["training_input"]["resume"] is True
    assert node.started[-1]["training_input"]["epoch"] == checkpoint["epoch"]
    assert (
        node.started[-1]["training_input"]["best_sha256"]
        == hashlib.sha256(b"original-best").hexdigest()
    )


@pytest.mark.parametrize(
    "parameters", [{"epochs": 6}, {"optimizer": "AdamW"}, {"lr0": 0.1}, {"pretrained": False}]
)
def test_resume_rejects_ignored_parameters(client, node, parameters):
    parent, dataset, _ = resumable_parent(client)
    response = client.post(
        "/api/v1/training/runs",
        json={
            "dataset_id": dataset["id"],
            "node_id": "local",
            "parameters": parameters,
            "source": {
                "kind": "run",
                "reference": parent["id"],
                "checkpoint": "resume",
                "mode": "resume",
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "RESUME_PARAMETERS_LOCKED"


def test_resume_rejects_different_dataset_and_stripped_checkpoint(client, node):
    parent, dataset, checkpoint = resumable_parent(client)
    other = upload_dataset(client, ultralytics_layout(), name="another version")
    payload = {
        "dataset_id": other["id"],
        "node_id": "local",
        "source": {
            "kind": "run",
            "reference": parent["id"],
            "checkpoint": "resume",
            "mode": "resume",
        },
    }
    assert (
        client.post("/api/v1/training/runs", json=payload).json()["code"]
        == "RESUME_DATASET_CHANGED"
    )
    repository = get_container().training
    current = repository.get_run(UUID(parent["id"]))
    repository.update(
        current.id, progress={**current.progress, "checkpoint": {**checkpoint, "resumable": False}}
    )
    assert (
        client.post(
            "/api/v1/training/runs", json={**payload, "dataset_id": dataset["id"]}
        ).status_code
        == 409
    )


def test_corrupt_input_fails_without_start_and_corrupt_collection_keeps_node(client, node):
    parent, dataset, checkpoint = resumable_parent(client)
    run = create_run(
        client,
        dataset["id"],
        source={"kind": "run", "reference": parent["id"], "checkpoint": "resume", "mode": "resume"},
        parameters={},
    )
    container = get_container()
    container.training_orchestrator.prepare(UUID(run["id"]))
    # A mismatched download must not delete the only good node copy.
    node.state = {
        "state": "stopped",
        "progress": {"checkpoint": checkpoint},
        "files": {checkpoint["file"]: 1},
        "metrics": [],
    }
    container.training_orchestrator.sync(UUID(run["id"]))
    current = get_run(client, run["id"])
    assert current["status"] == "running" and "校验失败" in current["stage"]
    assert f"/trainings/{run['id']}" not in node.deleted
    path = container.training_storage.run_directory(run["id"]) / ".source/model.pt"
    path.write_bytes(b"corrupt")
    container.training.update(UUID(run["id"]), status=TrainingStatus.QUEUED)
    count = len(node.started)
    container.training_orchestrator.prepare(UUID(run["id"]))
    assert get_run(client, run["id"])["status"] == "failed"
    assert len(node.started) == count


def test_foreign_training_source_is_rejected(client, node):
    parent, dataset, _ = resumable_parent(client)
    container = get_container()
    original = container.training.get_run(UUID(parent["id"]))
    project = client.post("/api/v1/projects", json={"name": "foreign"}).json()
    foreign = replace(original, id=UUID(int=900), project_id=UUID(project["id"]))
    container.training.add_run(foreign)
    response = client.post(
        "/api/v1/training/runs",
        json={
            "dataset_id": dataset["id"],
            "node_id": "local",
            "source": {"kind": "run", "reference": str(foreign.id)},
        },
    )
    assert response.status_code == 404
