import hashlib
import json
from uuid import UUID

import pytest
from test_training import create_run, get_run, ultralytics_layout, upload_dataset
from test_training_sources import upload_weight

from cv_platform.dependencies import get_container


def create_export(client, node, **overrides):
    dataset = upload_dataset(client, ultralytics_layout())
    payload = {
        "dataset_id": dataset["id"],
        "node_id": "local",
        "model": "yolo11n.pt",
        "parameters": {"imgsz": 64, "simplify": False},
        **overrides,
    }
    return client.post("/api/v1/exports", json=payload)


def test_export_runs_on_cpu_with_frozen_source_and_policy(client, node):
    node.gpu_list = []
    weight = upload_weight(client).json()
    response = create_export(
        client,
        node,
        source={"kind": "upload", "reference": weight["id"]},
        verification={"parameters": {"split": "val"}, "iterations": 2},
    )
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["progress"]["operation"] == "export" and job["gpu_device_ids"] == []
    assert job["parameters"]["format"] == "onnx" and "epochs" not in job["parameters"]
    assert client.get("/api/v1/training/runs").json() == []
    assert client.get("/api/v1/exports").json()[0]["id"] == job["id"]
    assert client.delete(f"/api/v1/training/weights/{weight['id']}").status_code == 204
    get_container().training_orchestrator.prepare(UUID(job["id"]))
    assert get_run(client, job["id"])["status"] == "running"
    assert node.started[-1]["operation"] == "export"
    assert node.started[-1]["export_verification"]["iterations"] == 2
    assert node.started[-1]["training_input"]["sha256"] == weight["sha256"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"parameters": {"format": "engine"}},
        {"parameters": {"half": True}},
        {"parameters": {"nms": True}},
        {"parameters": {"epochs": 3}},
        {"parameters": {"batch": -1}},
        {"parameters": {"batch": 2}},
        {"parameters": {"opset": 99}},
        {"parameters": {"imgsz": 65}},
        {"verification": {"parameters": {"split": "test"}}},
        {"verification": {"parameters": {"imgsz": 96}}},
        {"verification": {"parameters": {"fraction": 0.5}}},
        {"verification": {"parameters": {"classes": [99]}}},
        {"verification": {"iterations": 0}},
        {"verification": {"max_map_drop": -1}},
        {"gpu_device_ids": ["0"]},
    ],
)
def test_export_rejects_unsupported_or_unfair_comparisons(client, node, overrides):
    assert create_export(client, node, **overrides).status_code == 422


def finish_export(client, node, job, *, tamper=False, passed=True):
    report = json.dumps({"quality_gate": {"passed": passed}, "format": "onnx"}).encode()
    files = {
        "model/model.onnx": b"onnx graph",
        "model/nested/tensor.data": b"tensor",
        "export-report.json": report,
    }
    entries = {
        key: {"sha256": hashlib.sha256(value).hexdigest(), "size_bytes": len(value)}
        for key, value in files.items()
    }
    files["artifact-manifest.json"] = json.dumps({"version": 1, "files": entries}).encode()
    if tamper:
        files["model/model.onnx"] = b"corruption"

    def download(path, target):
        content = files[path.split("/files/", 1)[1]]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return len(content)

    node.download = download
    node.state = {
        "state": "completed" if passed else "failed",
        "progress": {"phase": "completed"},
        "files": {key: len(value) for key, value in files.items()},
        "metrics": [],
        "error": None if passed else "quality gate failed",
    }
    get_container().training_orchestrator.sync(UUID(job["id"]))


@pytest.mark.parametrize("passed", [True, False])
def test_export_collects_registered_directory_and_report_even_when_gate_fails(client, node, passed):
    job = create_export(client, node).json()
    get_container().training_orchestrator.prepare(UUID(job["id"]))
    finish_export(client, node, job, passed=passed)
    final = client.get(f"/api/v1/exports/{job['id']}").json()
    assert final["status"] == ("completed" if passed else "failed")
    assert final["progress"]["export_verification"]["max_map_drop"] == 0.01
    assert (
        client.get(f"/api/v1/exports/{job['id']}/report").json()["quality_gate"]["passed"] == passed
    )
    assert (
        client.get(f"/api/v1/exports/{job['id']}/artifacts/model/nested/tensor.data").content
        == b"tensor"
    )
    assert client.get(f"/api/v1/exports/{job['id']}/archive").status_code == 200
    assert node.deleted


def test_corrupt_export_is_not_committed_or_removed_from_node(client, node):
    job = create_export(client, node).json()
    get_container().training_orchestrator.prepare(UUID(job["id"]))
    finish_export(client, node, job, tamper=True)
    final = get_run(client, job["id"])
    assert final["status"] == "running" and "收回训练结果失败" in final["stage"]
    assert not node.deleted and not final["artifacts"]


def test_export_endpoints_do_not_accept_training_ids_or_foreign_jobs(client, node):
    dataset = upload_dataset(client, ultralytics_layout())
    training = create_run(client, dataset["id"])
    assert client.get(f"/api/v1/exports/{training['id']}").status_code == 404
    job = create_export(client, node).json()
    project = client.post("/api/v1/projects", json={"name": "other"}).json()
    headers = {"X-Project-ID": project["id"]}
    assert client.get("/api/v1/exports", headers=headers).json() == []
    assert client.get(f"/api/v1/exports/{job['id']}/report", headers=headers).status_code == 404
    assert client.delete(f"/api/v1/exports/{job['id']}", headers=headers).status_code == 404
