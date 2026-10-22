import json
from uuid import UUID

import pytest
from cv_algorithm_sdk import AlgorithmManifest, AlgorithmResult, TaskStatus
from pydantic import TypeAdapter
from test_evaluations_api import upload

from cv_platform.core.project_context import DEFAULT_PROJECT_ID
from cv_platform.dependencies import get_container
from cv_platform.modules.algorithms.domain.entities import AlgorithmVersion


def dataset_payload(asset_id, annotations, task_type="object_detection"):
    return {
        "name": "review-repro", "task_type": task_type,
        "annotations_filename": "annotations.json", "annotations": json.dumps(annotations),
        "asset_ids": [asset_id],
    }


def test_dataset_matches_the_current_upload_filename_after_deduplication(client):
    original = upload(client, "old-name.png")[0]
    renamed = upload(client, "dataset-name.png")[0]
    assert original["id"] == renamed["id"]
    payload = dataset_payload(
        renamed["id"], {"items": [{"file": "dataset-name.png", "label": "cat"}]},
        "classification",
    )
    payload["asset_names"] = {renamed["id"]: "dataset-name.png"}
    response = client.post("/api/v1/evaluations/datasets", json=payload)
    assert response.status_code == 201, response.text
    stored = client.get(f"/api/v1/assets/{original['id']}").json()
    assert stored["original_name"] == "old-name.png"


def test_structurally_invalid_coco_has_a_validation_response(client):
    asset = upload(client, "a.png")[0]
    client._transport.raise_server_exceptions = False
    response = client.post("/api/v1/evaluations/datasets", json=dataset_payload(
        asset["id"], {
            "images": [{"id": 1, "file_name": "a.png"}],
            "categories": [{"id": 1, "name": "cat"}],
            "annotations": [{"image_id": 1, "category_id": 1}],
        },
    ))
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "ANNOTATIONS_INVALID"


def test_invalid_detection_is_not_silently_deleted_from_ground_truth(client):
    asset = upload(client, "a.png")[0]
    response = client.post("/api/v1/evaluations/datasets", json=dataset_payload(
        asset["id"], {"items": [{"file": "a.png", "detections": [
            {"bbox": [0, 0, 20, 20]},
        ]}]},
    ))
    assert response.status_code == 400, response.text


@pytest.mark.parametrize("task_type,entry", [
    ("object_detection", {"detections": [None]}),
    ("object_detection", {"detections": [{"label": "cat", "bbox": [0, 0, "NaN", 20]}]}),
    ("pose_estimation", {"keypoints": [{"name": "nose", "x": "bad", "y": 0}]}),
    ("pose_estimation", {"keypoints": [{"name": "nose", "x": 0}]}),
    ("pose_estimation", {"keypoints": []}),
    ("pose_estimation", {"keypoints": [{"name": "nose", "x": 0, "y": 0}], "threshold": -1}),
    ("pose_estimation", {"keypoints": [{"name": "nose", "x": 0, "y": 0}] * 2}),
    ("classification", {"label": None}),
    ("segmentation", {"labels": [None]}),
    ("ocr", {"text": None}),
])
def test_invalid_ground_truth_is_rejected(client, task_type, entry):
    asset = upload(client, "a.png")[0]
    response = client.post("/api/v1/evaluations/datasets", json=dataset_payload(
        asset["id"], {"items": [{"file": "a.png", **entry}]}, task_type,
    ))
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "ANNOTATIONS_INVALID"
    assert client.get("/api/v1/evaluations/datasets").json() == []


@pytest.mark.parametrize("document", [
    {"images": None, "annotations": []},
    {"images": [None], "annotations": [], "categories": []},
    {"images": [{"id": 1, "file_name": "a.png"}], "annotations": [None], "categories": []},
    {"images": [{"id": 1, "file_name": "a.png"}], "categories": [{"id": 1, "name": "cat"}],
     "annotations": [{"image_id": 2, "category_id": 1, "bbox": [0, 0, 10, 10]}]},
    {"items": [{"file": "a.png", "label": "cat"}, {"file": "dir/A.PNG", "label": "dog"}]},
])
def test_malformed_or_ambiguous_documents_are_rejected(client, document):
    asset = upload(client, "a.png")[0]
    response = client.post("/api/v1/evaluations/datasets", json=dataset_payload(
        asset["id"], document,
    ))
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "ANNOTATIONS_INVALID"


def test_pose_pck_penalizes_missing_ground_truth_keypoints(client):
    asset = upload(client, "pose.png")[0]
    dataset = client.post("/api/v1/evaluations/datasets", json=dataset_payload(
        asset["id"], {"items": [{"file": "pose.png", "keypoints": [
            {"name": "nose", "x": 10, "y": 10},
            {"name": "left_eye", "x": 15, "y": 10},
            {"name": "right_eye", "x": 5, "y": 10},
        ]}]}, "pose_estimation",
    )).json()
    algorithm = AlgorithmVersion.available(AlgorithmManifest.model_validate({
        "id": "review-pose", "name": "pose", "version": "1.0.0",
        "task_type": "pose_estimation", "runtime": {"framework": "onnx", "device": "cpu"},
        "input": {"media_types": ["image/png"]}, "output": {"type": "pose_estimation"},
    }), "cv-platform/review-pose:1.0.0", project_id=DEFAULT_PROJECT_ID)
    get_container().algorithms.add(algorithm)
    run = client.post("/api/v1/evaluations/runs", json={
        "dataset_id": dataset["id"], "algorithm_version_ids": [str(algorithm.id)],
    }).json()
    stored = get_container().evaluations.get_run(UUID(run["id"]))
    task_id = UUID(next(iter(stored.task_ids.values())))
    result = TypeAdapter(AlgorithmResult).validate_python({
        "schema_version": "1.0", "request_id": "review-pose", "type": "pose_estimation",
        "algorithm": {"id": "pose", "version": "1.0.0"},
        "input": {"width": 100, "height": 100},
        "timing": {"preprocess_ms": 1, "inference_ms": 1, "postprocess_ms": 1},
        "data": {"instances": [{"score": 0.9, "keypoints": [
            {"name": "nose", "x": 10, "y": 10, "score": 0.9},
        ]}]},
    })
    tasks = get_container().tasks
    running = tasks.get(task_id).transition(TaskStatus.RUNNING)
    tasks.save(running)
    tasks.save(running.transition(TaskStatus.COMPLETED, result=result))
    response = client.get(f"/api/v1/evaluations/runs/{run['id']}")
    assert response.status_code == 200, response.text
    metrics = response.json()["report"]["algorithms"][0]["metrics"]
    assert metrics["pck"] == pytest.approx(1 / 3), metrics
