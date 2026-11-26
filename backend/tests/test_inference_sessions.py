import io
import json
from uuid import UUID, uuid4

import pytest
from cv_algorithm_sdk import TaskStatus
from PIL import Image
from test_tasks_api import asset_id as asset_id

from cv_platform.dependencies import get_container
from cv_platform.modules.inference.service import now
from cv_platform.modules.inference.tracking import IoUTracker


@pytest.fixture
def no_execute(client, monkeypatch):
    service = get_container().inference
    monkeypatch.setattr(service.executor, "submit", lambda *args, **kw: None)
    return service


def create(client, asset_id=None, **kw):
    model = client.get("/api/v1/algorithms").json()[0]
    payload = {
        "algorithm_version_id": model["id"],
        "kind": "images",
        "asset_ids": [asset_id] if asset_id else [],
        **kw,
    }
    return client.post("/api/v1/inference/sessions", json=payload)


def test_create_validates_and_freezes_config(client, asset_id, no_execute):
    r = create(client, asset_id)
    assert r.status_code == 201, r.text
    state = r.json()
    assert state["config"]["parameters"]["confidence"] == 0.5
    assert state["inputs"][0]["sha256"]
    assert create(client, asset_id, parameters={"unknown": True}).status_code == 400
    assert create(client, asset_id, tracking=True).status_code == 400
    assert create(client, str(uuid4())).status_code == 404
    assert create(client, asset_id, max_frames=10001).status_code == 422
    assert client.get("/api/v1/inference/sessions").json()[0]["id"] == state["id"]


def test_stop_idempotence_and_project_isolation(client, asset_id, no_execute):
    state = create(client, asset_id).json()
    key = state["id"]
    assert client.post(f"/api/v1/inference/sessions/{key}/stop").json()["status"] == "stopping"
    assert client.post(f"/api/v1/inference/sessions/{key}/stop").status_code == 200
    from cv_platform.core.errors import ApplicationError

    with pytest.raises(ApplicationError):
        no_execute.store.get(key, uuid4())
    no_execute.recover()
    assert no_execute.store.get(key)["status"] == "interrupted"


def test_browser_ingress_is_bounded_and_stop_closes_it(client, no_execute):
    state = create(client, kind="browser", tracking=True).json()
    key = state["id"]
    data = io.BytesIO()
    Image.new("RGB", (64, 64)).save(data, format="JPEG")
    path = f"/api/v1/inference/sessions/{key}/frames"
    assert client.post(path, files={"file": ("frame.jpg", b"bad")}).status_code == 400
    assert client.post(path, files={"file": ("frame.jpg", data.getvalue())}).status_code == 202
    assert client.post(path, files={"file": ("frame.jpg", data.getvalue())}).status_code == 429
    client.post(f"/api/v1/inference/sessions/{key}/stop")
    assert client.post(path, files={"file": ("frame.jpg", data.getvalue())}).status_code == 409


def test_tracker_association_class_and_gap():
    tracker = IoUTracker()
    person = {"label": "person", "score": 0.8, "bbox": [0, 0, 100, 100]}
    first = tracker.update([person], 0)[0]
    second = tracker.update([{**person, "bbox": [5, 0, 105, 100]}], 1)[0]
    assert first["track_id"] == second["track_id"] and len(second["trail"]) == 2
    assert tracker.update([{**person, "label": "car"}], 1.1)[0]["track_id"] != first["track_id"]
    assert tracker.update([person], 5)[0]["track_id"] != first["track_id"]
    assert IoUTracker().update([person], 0)[0]["track_id"] == 1


def test_report_downloads_share_scope_and_no_accuracy(client, asset_id, no_execute):
    state = create(client, asset_id).json()
    key = state["id"]
    for i in range(3):
        no_execute.store.frame(
            key,
            {
                "index": i,
                "source_index": i * 10,
                "timestamp_ms": i * 1000,
                "segment": 0,
                "asset_id": asset_id,
                "input_name": "样例.png",
                "input_sha256": "a" * 64,
                "task_id": str(uuid4()),
                "status": "completed",
                "error": None,
                "tracks": [],
                "result": {
                    "type": "object_detection",
                    "timing": {"inference_ms": 2},
                    "data": {"detections": [{"label": "人", "score": 0.8, "bbox": [0, 0, 10, 10]}]},
                },
            },
        )
    no_execute.update(key, processed=3, status="completed", finished_at=now())
    root = f"/api/v1/inference/sessions/{key}"
    report = client.get(root + "/report?start=1&end=2").json()
    assert report["summary"]["processed"] == 1 and report["summary"]["detections"] == 1
    assert report["summary"]["tracks"] is None
    assert "map50" not in json.dumps(report)
    for name, magic in [
        ("report.pdf", b"%PDF"),
        ("classes.png", b"\x89PNG"),
        ("bundle.zip", b"PK"),
    ]:
        r = client.get(root + "/download/" + name)
        assert r.status_code == 200, r.text[:100]
        assert r.content.startswith(magic)
    r = client.get(root + "/download/metrics.csv?start=1&end=2")
    assert len(r.content.decode("utf-8-sig").splitlines()) == 2
    assert client.get(root + "/report?start=2&end=1").status_code == 400
    assert client.get(root + "/files/config.json").status_code == 404
    assert client.get(root + "/download/secret").status_code == 404


def test_retry_only_unfinished_inputs(client, asset_id, no_execute):
    state = create(client, asset_id).json()
    key = state["id"]
    assert client.post(f"/api/v1/inference/sessions/{key}/retry").status_code == 409
    no_execute.update(key, status="failed")
    retry = client.post(f"/api/v1/inference/sessions/{key}/retry").json()
    assert retry["config"]["retry_of"] == key


def test_cleanup_only_removes_generated_artifacts(client, asset_id, no_execute):
    state = create(client, asset_id).json()
    key = state["id"]
    root = no_execute.root / key
    root.mkdir(exist_ok=True)
    (root / "preview.jpg").write_bytes(b"test")
    no_execute.update(key, status="completed", expires_at="2000-01-01T00:00:00+00:00")
    no_execute.cleanup()
    assert not root.exists()
    assert no_execute.store.get(key)["artifacts_expired"]
    assert client.get(f"/api/v1/assets/{asset_id}").status_code == 200


def test_real_session_loop_freezes_results_and_stops(client, asset_id, no_execute, monkeypatch):
    from cv_algorithm_sdk import ObjectDetectionResult

    container = get_container()

    def submit(task_id):
        task = container.tasks.get(UUID(task_id))
        result = ObjectDetectionResult.model_validate(
            {
                "request_id": task_id,
                "algorithm": {"id": "test", "version": "1"},
                "input": {"width": 16, "height": 12},
                "timing": {"inference_ms": 3},
                "data": {"detections": [{"label": "person", "score": 0.9, "bbox": [1, 1, 10, 10]}]},
            }
        )
        container.tasks.save(task.transition(TaskStatus.COMPLETED, result=result))

    monkeypatch.setattr(container.task_queue, "submit", submit)
    state = create(client, asset_id).json()
    no_execute.execute(state["id"], container)
    done = no_execute.store.get(state["id"])
    assert done["status"] == "completed", done
    assert done["processed"] == 1 and done["failed"] == 0
    frame = no_execute.store.frames(state["id"])[0]
    assert frame["result"]["data"]["detections"][0]["label"] == "person"
    assert (no_execute.root / state["id"] / "preview.jpg").exists()
    assert client.get(
        f"/api/v1/inference/sessions/{state['id']}/download/report.pdf"
    ).content.startswith(b"%PDF")
