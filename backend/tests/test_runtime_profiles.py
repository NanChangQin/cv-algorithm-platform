# ruff: noqa: F811
import json
from uuid import UUID

import pytest
from cv_algorithm_sdk import TaskStatus
from cv_algorithm_sdk.export_artifacts import write_manifest
from test_deployments import _png, cluster, current, detector_id, run  # noqa: F401
from test_exports import create_export

from cv_platform.dependencies import get_container
from cv_platform.modules.training.domain import TrainingStatus


def test_old_manager_cannot_silently_reuse_shared_instances(monkeypatch):
    from cv_platform.core.errors import ApplicationError
    from cv_platform.modules.instances.domain.models import RuntimeRequest
    from cv_platform.modules.instances.infrastructure.http_gateway import (
        HttpAlgorithmManagerGateway,
    )

    gateway = HttpAlgorithmManagerGateway("http://node")
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path))
        raise ApplicationError("NOT_FOUND", "old node", 404)

    monkeypatch.setattr(gateway, "_request", request)
    runtime = RuntimeRequest("version", "test", "image", "cv-algorithm-test", 8000, "cpu", 1024,
                             runtime_scope="a" * 32)
    with pytest.raises(ApplicationError, match="请先升级 Manager"):
        gateway.ensure_running(runtime)
    assert calls == [("GET", "/instances/capabilities")]


def published(client, node, *, batch=1, passed=True, dynamic=False):
    job = create_export(client, node).json()
    container = get_container()
    record = container.training.get_run(UUID(job["id"]))
    root = container.training_storage.run_directory(record.id)
    (root / "model/nested").mkdir(parents=True, exist_ok=True)
    (root / "model/model.onnx").write_bytes(b"graph")
    (root / "model/nested/tensor.data").write_bytes(b"tensor")
    from cv_algorithm_sdk.export_artifacts import digest

    report = {
        "format": "onnx",
        "precision": "fp32",
        "artifact": "model/model.onnx",
        "artifact_sha256": digest(root / "model/model.onnx"),
        "quality_gate": {"passed": passed},
        "smoke_test": {"passed": True},
        "input_shape": [batch, 3, 64, 64],
        "dynamic": dynamic,
        "class_names": ["cat"],
    }
    (root / "export-report.json").write_text(json.dumps(report), encoding="utf-8")
    write_manifest(root, ["model/model.onnx", "model/nested/tensor.data", "export-report.json"])
    container.training.update(record.id, status=TrainingStatus.COMPLETED)
    response = client.post(f"/api/v1/exports/{record.id}/publish", json={"key": "cat-onnx"})
    return job, response


def test_export_publish_is_independent_and_has_backend_specific_parameters(client, node):
    job, response = published(client, node)
    assert response.status_code == 201, response.text
    model = response.json()
    assert model["framework"] == "onnx" and model["device"] == "cpu"
    assert model["parameters"]["image_size"]["json_schema"] == {"const": 64}
    assert "compile" not in model["parameters"] and "quantize" not in model["parameters"]
    container = get_container()
    target = container.model_store.directory(UUID(model["id"]))
    assert (target / "model/nested/tensor.data").read_bytes() == b"tensor"
    assert client.delete(f"/api/v1/exports/{job['id']}").status_code == 204
    assert (target / "model/model.onnx").read_bytes() == b"graph"
    algorithm = container.algorithms.get(UUID(model["id"]))
    assert len(container.model_store.files(algorithm)) == 5


def test_dynamic_graph_can_deploy_single_images_despite_export_batch_size(client, node):
    _, response = published(client, node, batch=2, dynamic=True)
    assert response.status_code == 201, response.text
    parameters = response.json()["parameters"]
    assert "const" not in parameters["image_size"]["json_schema"]


@pytest.mark.parametrize("options", [{"batch": 2}, {"passed": False}])
def test_unusable_exports_cannot_publish(client, node, options):
    _, response = published(client, node, **options)
    assert response.status_code == 409, response.text


def test_publish_rechecks_all_artifact_hashes(client, node):
    job, response = published(client, node)
    assert response.status_code == 201
    root = get_container().training_storage.run_directory(UUID(job["id"]))
    (root / "model/nested/tensor.data").write_bytes(b"tamper")
    response = client.post(f"/api/v1/exports/{job['id']}/publish", json={"key": "tampered"})
    assert response.status_code == 409


def test_profile_defaults_locks_and_binding_survive_retry(client, cluster):
    algorithm_id = detector_id(client)
    response = client.post(
        "/api/v1/deployments",
        json={
            "algorithm_version_id": algorithm_id,
            "node_id": "dell",
            "runtime_profile": {
                "cpu_count": 2.5,
                "memory_mb": 4096,
                "defaults": {"confidence": 0.73},
                "allowed_overrides": [],
            },
        },
    )
    assert response.status_code == 201, response.text
    deployment = response.json()
    run(cluster, deployment)
    assert current(client, deployment["id"])["runtime_profile"]["cpu_count"] == 2.5
    asset = client.post(
        "/api/v1/assets/upload", files=[("files", ("a.png", _png(), "image/png"))]
    ).json()["assets"][0]
    payload = {
        "algorithm_version_id": algorithm_id,
        "asset_id": asset["id"],
        "deployment_id": deployment["id"],
    }
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["deployment_id"] == deployment["id"] and task["parameters"]["confidence"] == 0.73
    denied = client.post("/api/v1/tasks", json={**payload, "parameters": {"confidence": 0.2}})
    assert denied.status_code == 400 and denied.json()["code"] == "PARAMETER_INVALID"
    tasks = get_container().tasks
    original = tasks.get(UUID(task["id"]))
    tasks.save(original.transition(TaskStatus.FAILED))
    retry = client.post(f"/api/v1/tasks/{original.id}/retry")
    assert retry.status_code == 201 and retry.json()["deployment_id"] == deployment["id"]
    # Deletion must never turn a bound task into automatic scheduling.
    assert client.delete(f"/api/v1/deployments/{deployment['id']}").status_code == 204
    executor = get_container().task_executor
    executor.execute_now(UUID(retry.json()["id"]))
    assert tasks.get(UUID(retry.json()["id"])).error_code == "DEPLOYMENT_NOT_FOUND"


@pytest.mark.parametrize(
    "profile",
    [
        {"cpu_count": 0},
        {"shm_mb": 0},
        {"workers": 2},
        {"prediction_timeout_seconds": 601},
        {"threads": 0},
        {"defaults": {"unknown": False}},
        {"allowed_overrides": ["unknown"]},
    ],
)
def test_profile_rejects_invalid_or_unsupported_settings(client, cluster, profile):
    response = client.post(
        "/api/v1/deployments",
        json={
            "algorithm_version_id": detector_id(client),
            "node_id": "dell",
            "runtime_profile": profile,
        },
    )
    assert response.status_code in {400, 422}, response.text


def test_bound_task_uses_exact_instance_without_rescheduling(client, cluster, monkeypatch):
    from datetime import UTC, datetime

    from cv_algorithm_sdk import (
        AlgorithmIdentity,
        ImageMetadata,
        ObjectDetectionData,
        ObjectDetectionResult,
        TimingMetadata,
    )
    from test_deployments import deploy

    from cv_platform.modules.instances.domain.models import RuntimeInstance

    algorithm_id = detector_id(client)
    deployment = deploy(client, algorithm_id)
    run(cluster, deployment)
    asset = client.post(
        "/api/v1/assets/upload", files=[("files", ("a.png", _png(), "image/png"))]
    ).json()["assets"][0]
    response = client.post(
        "/api/v1/tasks",
        json={
            "algorithm_version_id": algorithm_id,
            "asset_id": asset["id"],
            "deployment_id": deployment["id"],
        },
    )
    executor = get_container().task_executor
    now = datetime.now(UTC)
    expected = RuntimeInstance(
        "container-1",
        algorithm_id,
        "image",
        "cv-algorithm-test",
        "http://test",
        "healthy",
        "gpu",
        now,
        now,
        node_id="dell",
        runtime_scope=UUID(deployment["id"]).hex,
    )
    monkeypatch.setattr(executor._instances, "get_running", lambda *args: expected)
    monkeypatch.setattr(executor._instances, "touch", lambda *args: expected)
    used = []

    def predict(instance, request_id, asset_uri, parameters):
        used.append(instance.id)
        return ObjectDetectionResult(
            request_id=request_id,
            algorithm=AlgorithmIdentity(id="test", version="1.0.0"),
            input=ImageMetadata(width=16, height=16),
            timing=TimingMetadata(inference_ms=1),
            data=ObjectDetectionData(detections=[]),
        )

    monkeypatch.setattr(executor._predictions, "predict", predict)
    executor.execute_now(UUID(response.json()["id"]))
    task = get_container().tasks.get(UUID(response.json()["id"]))
    assert task.status is TaskStatus.COMPLETED and used == ["container-1"]
    assert len(cluster.started) == 1


@pytest.mark.parametrize("ready", [True, False])
def test_onnx_readiness_is_required_and_failure_stops_instance(
    client, node, cluster, monkeypatch, ready
):
    _, response = published(client, node)
    model = response.json()
    cluster.nodes["dell"].images.add(model["image"])
    target = cluster.nodes["dell"]
    original = target.request

    def request(method, path, **kwargs):
        if path.endswith("/readiness"):
            return {
                "ready": ready,
                "smoke_passed": ready,
                "backend": "onnxruntime",
                "warmup_runs": 1,
                "threads": 1,
                "providers": ["CPUExecutionProvider"],
                "artifact_sha256": model["lineage"]["export_report"]["artifact_sha256"],
            }
        return original(method, path, **kwargs)

    monkeypatch.setattr(target, "request", request)
    response = client.post(
        "/api/v1/deployments", json={"algorithm_version_id": model["id"], "node_id": "dell"}
    )
    deployment = response.json()
    run(cluster, deployment)
    result = current(client, deployment["id"])
    assert result["status"] == ("running" if ready else "failed"), result
    assert bool(result["readiness"]) is ready
    assert cluster.stopped == ([] if ready else ["container-1"])
