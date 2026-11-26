import io
import json
import time
import zipfile
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_training import RESULTS, create_run, ultralytics_layout, upload_dataset

from cv_platform.core.errors import ApplicationError
from cv_platform.dependencies import get_container
from cv_platform.modules.training import batches
from cv_platform.modules.training.domain import TrainingStatus


def fixture_run(client, name="批量任务", *, weights=True, status=TrainingStatus.COMPLETED):
    dataset = upload_dataset(client, ultralytics_layout())
    run = create_run(client, dataset["id"], name=name)
    container = get_container()
    files = {}
    if weights:
        path = container.training_storage.run_directory(run["id"]) / "weights/best.pt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"weight-bytes")
        files["weights/best.pt"] = path.stat().st_size
    container.training.update(UUID(run["id"]), status=status, metrics=RESULTS, artifacts=files)
    return run


def start(client, ids, action="download", content="weights", request_id=None):
    payload = dict(
        request_id=str(request_id or uuid4()), run_ids=ids, action=action, content=content
    )
    response = client.post("/api/v1/training/batches", json=payload)
    assert response.status_code == 202, response.text
    return payload


def wait(client, payload):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get("/api/v1/training/batches/" + payload["request_id"])
        assert response.status_code == 200, response.text
        state = response.json()
        if state["status"] in {"completed", "failed"}:
            return state
        time.sleep(0.01)
    pytest.fail("batch did not complete")


def test_batch_weights_unique_folders_partial_success_and_idempotency(client, node):
    a = fixture_run(client, "../../重复名称")
    b = fixture_run(client, "../../重复名称")
    missing = fixture_run(client, weights=False)
    payload = start(client, [a["id"], a["id"], b["id"], missing["id"]])
    state = wait(client, payload)
    assert state["total"] == state["processed"] == 3
    assert [item["status"] for item in state["items"]] == ["success", "success", "skipped"]
    response = client.post("/api/v1/training/batches", json=payload)
    assert response.status_code == 202
    assert response.json()["id"] == state["id"]
    archive = client.get(f"/api/v1/training/batches/{state['id']}/download")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as result:
        paths = [name for name in result.namelist() if name.endswith("best.pt")]
        assert len(paths) == 2 and len(set(paths)) == 2
        assert all(".." not in name.split("/") for name in paths)
        assert all(result.read(name) == b"weight-bytes" for name in paths)
        assert json.loads(result.read("操作结果.json"))[-1]["reason"] == "尚无模型权重"


def test_batch_delete_preflight_skip_active_and_failed_cleanup_retry(client, node, monkeypatch):
    good = fixture_run(client)
    active = fixture_run(client, status=TrainingStatus.RUNNING)
    broken = fixture_run(client)
    original = node.request

    def request(method, path, **kwargs):
        if method == "DELETE" and broken["id"] in path:
            raise ApplicationError("NODE_DOWN", "offline", 503)
        return original(method, path, **kwargs)

    monkeypatch.setattr(node, "request", request)
    ids = [good["id"], active["id"], broken["id"]]
    preview = client.post("/api/v1/training/batches/delete-preview", json={"run_ids": ids}).json()
    assert [item["eligible"] for item in preview["items"]] == [True, False, True]
    assert client.get("/api/v1/training/runs/" + good["id"]).status_code == 200
    payload = start(client, ids, "delete")
    state = wait(client, payload)
    assert [item["status"] for item in state["items"]] == ["success", "skipped", "failed"]
    assert client.get("/api/v1/training/runs/" + good["id"]).status_code == 404
    assert client.get("/api/v1/training/runs/" + broken["id"]).status_code == 200
    assert client.get("/api/v1/training/runs/" + active["id"]).status_code == 200
    assert client.post("/api/v1/training/batches", json=payload).json()["id"] == state["id"]
    monkeypatch.setattr(node, "request", original)
    retried = wait(client, start(client, [broken["id"]], "delete"))
    assert retried["items"][0]["status"] == "success"


@pytest.mark.parametrize(
    "content, expected",
    [("metrics", "metrics.csv"), ("reports", "report.pdf"), ("all", "report/report.pdf")],
)
def test_batch_download_content(client, node, content, expected):
    run = fixture_run(client)
    state = wait(client, start(client, [run["id"]], content=content))
    assert state["items"][0]["status"] == "success", state
    response = client.get(f"/api/v1/training/batches/{state['id']}/download")
    with zipfile.ZipFile(io.BytesIO(response.content)) as result:
        assert any(name.endswith("/" + expected) for name in result.namelist())
        if content != "all":
            assert not any(name.endswith(".pt") for name in result.namelist())


def test_project_isolation_validated_before_any_deletion(client, node):
    own = fixture_run(client)
    foreign = fixture_run(client)
    repo = get_container().training
    project = client.post("/api/v1/projects", json={"name": "另一项目"}).json()
    foreign_copy = replace(
        repo.get_run(UUID(foreign["id"])), id=uuid4(), project_id=UUID(project["id"])
    )
    repo.add_run(foreign_copy)
    foreign["id"] = str(foreign_copy.id)
    response = client.post(
        "/api/v1/training/batches",
        json={
            "request_id": str(uuid4()),
            "run_ids": [own["id"], foreign["id"]],
            "action": "delete",
        },
    )
    assert response.status_code == 404
    assert repo.get_run(UUID(own["id"])) is not None
    assert not node.deleted


def test_legacy_dependency_protection_and_snapshot_release(client, node):
    parent = fixture_run(client)
    child = fixture_run(client, status=TrainingStatus.QUEUED)
    container = get_container()
    container.training.update(
        UUID(child["id"]), progress={"source": {"kind": "run", "reference": parent["id"]}}
    )
    assert client.delete("/api/v1/training/runs/" + parent["id"]).status_code == 409
    frozen = container.training_storage.run_directory(child["id"]) / ".source/model.pt"
    frozen.parent.mkdir(parents=True)
    frozen.write_bytes(b"independent copy")
    assert client.delete("/api/v1/training/runs/" + parent["id"]).status_code == 204


def test_interrupted_job_reports_unfinished_items_and_expiry(client, node, monkeypatch):
    run = fixture_run(client)
    state = wait(client, start(client, [run["id"]]))
    root = get_container().training_storage.root / "batches"
    path = next(root.glob(f"*/{state['id']}/state.json"))
    raw = json.loads(path.read_text())
    raw.update(status="running", process="previous-process", finished_at=None, download_ready=False)
    path.write_text(json.dumps(raw))
    response = client.get(f"/api/v1/training/batches/{state['id']}")
    assert response.json()["status"] == "failed"
    assert "服务重启" in response.json()["error"]
    raw = json.loads(path.read_text())
    raw["finished_at"] = time.time() - batches.TTL - 1
    path.write_text(json.dumps(raw))
    assert client.get(f"/api/v1/training/batches/{state['id']}/download").status_code == 410


def test_batch_job_is_private_to_requesting_user(client, node):
    run = fixture_run(client)
    state = wait(client, start(client, [run["id"]]))
    root = get_container().training_storage.root / "batches"
    path = next(root.glob(f"*/{state['id']}/state.json"))
    raw = json.loads(path.read_text())
    raw["actor"] = "another-user"
    path.write_text(json.dumps(raw))
    assert client.get(f"/api/v1/training/batches/{state['id']}").status_code == 404
    assert client.get(f"/api/v1/training/batches/{state['id']}/download").status_code == 404


def test_live_job_lock_prevents_false_restart_and_duplicate_execution(client, node, monkeypatch):
    from threading import Event

    run = fixture_run(client)
    entered, release = Event(), Event()
    original = batches.package_task

    def delayed(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(batches, "package_task", delayed)
    payload = start(client, [run["id"]])
    try:
        assert entered.wait(5)
        state = client.get("/api/v1/training/batches/" + payload["request_id"]).json()
        assert state["status"] == "running"
        # Different API process identity must not invalidate an actively held lock.
        monkeypatch.setattr(batches, "_process", "another-process")
        same = client.post("/api/v1/training/batches", json=payload)
        assert same.status_code == 202 and same.json()["status"] == "running"
    finally:
        release.set()
    assert wait(client, payload)["items"][0]["status"] == "success"


def test_published_model_copy_is_retained_and_incomplete_copy_blocks_delete(client, node):
    from test_model_publish import finished_run, publish

    run = finished_run(client, node)
    model = publish(client, run["id"], key="batch-protected").json()
    directory = get_container().model_store.directory(UUID(model["id"]))
    weight = directory / "model.pt"
    content = weight.read_bytes()
    weight.unlink()
    preview = client.post(
        "/api/v1/training/batches/delete-preview", json={"run_ids": [run["id"]]}
    ).json()
    assert not preview["items"][0]["eligible"]
    assert "独立文件不完整" in preview["items"][0]["reason"]
    weight.write_bytes(content)
    state = wait(client, start(client, [run["id"]], "delete"))
    assert state["items"][0]["status"] == "success"
    assert weight.read_bytes() == content
    assert get_container().training.get_dataset(UUID(run["dataset_id"])) is not None
