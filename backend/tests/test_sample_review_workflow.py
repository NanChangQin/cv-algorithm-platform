import hashlib
from uuid import UUID, uuid4

import pytest
from test_evaluations_api import coco, complete, create_dataset, detector_id, upload
from test_training import ultralytics_layout, upload_dataset

from cv_platform.core.errors import ApplicationError
from cv_platform.dependencies import get_container


def run_evaluation(client, dataset):
    algorithm = detector_id(client)
    response = client.post(
        "/api/v1/evaluations/runs",
        json={"dataset_id": dataset["id"], "algorithm_version_ids": [algorithm]},
    )
    assert response.status_code == 201, response.text
    run = response.json()
    stored = get_container().evaluations.get_run(UUID(run["id"]))
    return run, algorithm, list(stored.task_ids.values())


def test_error_filter_and_matching_are_consistent(client):
    assets = upload(client, "a.png", "b.png")
    dataset = create_dataset(
        client,
        assets,
        coco(("a.png", [("person", [0, 0, 20, 20])]), ("b.png", [("cat", [10, 10, 30, 30])])),
    )
    run, algorithm, tasks = run_evaluation(client, dataset)
    complete(
        tasks[0],
        [
            {"label": "person", "score": 0.9, "bbox": [0, 0, 20, 20]},
            {"label": "person", "score": 0.8, "bbox": [0, 0, 20, 20]},
        ],
    )
    complete(tasks[1], [{"label": "dog", "score": 0.8, "bbox": [10, 10, 40, 40]}])
    url = f"/api/v1/evaluations/runs/{run['id']}/samples?algorithm_version_id={algorithm}"
    response = client.get(url).json()
    assert response["total"] == 2
    assert sum(s["tp"] for s in response["items"]) == 1
    assert sum(s["fp"] for s in response["items"]) == 2
    assert sum(s["fn"] for s in response["items"]) == 1
    assert client.get(url + "&error_type=fn&label=cat").json()["total"] == 1
    assert client.get(url + "&error_type=fp&label=cat").json()["total"] == 0
    assert len(client.get(url + "&offset=1&limit=1").json()["items"]) == 1
    assert client.get(url.replace(algorithm, str(uuid4()))).status_code == 404


def test_review_new_version_keeps_parent_split_and_requires_human_review(client, node):
    dataset = upload_dataset(client, ultralytics_layout())
    old_file = get_container().training_storage.path(UUID(dataset["id"]))
    original_hash = hashlib.sha256(old_file.read_bytes()).hexdigest()
    response = client.post(
        "/api/v1/evaluations/datasets/from-training",
        json={"dataset_id": dataset["id"], "split": "val"},
    )
    assert response.status_code == 201, response.text
    run, _, tasks = run_evaluation(client, response.json())
    complete(tasks[0], [{"label": "cat", "score": 0.8, "bbox": [1, 1, 5, 5]}])
    response = client.post(
        "/api/v1/training/reviews", json={"task_id": tasks[0], "evaluation_id": run["id"]}
    )
    assert response.status_code == 201, response.text
    review = response.json()
    assert review["status"] == "draft" and review["boxes"][0]["label"] == "dog"
    assert (
        client.post("/api/v1/training/reviews", json={"task_id": tasks[0]}).json()["id"]
        == review["id"]
    )
    payload = {"dataset_id": dataset["id"], "review_ids": [review["id"]], "target_split": "train"}
    assert client.post("/api/v1/training/reviews/publish", json=payload).status_code == 409
    save = {
        "version": review["version"],
        "boxes": [{"label": "cat", "bbox": [1, 1, 10, 10]}],
        "reviewed": True,
    }
    bad = {**save, "boxes": [{"label": "cat", "bbox": [0, 0, 10000, 10]}]}
    assert client.put(f"/api/v1/training/reviews/{review['id']}", json=bad).status_code == 400
    response = client.put(f"/api/v1/training/reviews/{review['id']}", json=save)
    assert response.status_code == 200, response.text
    assert client.put(f"/api/v1/training/reviews/{review['id']}", json=save).status_code == 409
    assert client.post("/api/v1/training/reviews/publish", json=payload).status_code == 400
    response = client.post(
        "/api/v1/training/reviews/publish", json={**payload, "acknowledge_evaluation_reuse": True}
    )
    assert response.status_code == 201, response.text
    new = response.json()
    assert new["parent_id"] == dataset["id"] and new["version"] == 2
    assert (
        new["stats"]["splits"]["train"]["images"] == dataset["stats"]["splits"]["train"]["images"]
    )
    assert new["stats"]["splits"]["val"]["images"] == 1
    assert hashlib.sha256(old_file.read_bytes()).hexdigest() == original_hash
    index = get_container().training_storage.index(UUID(new["id"]))
    val = next(item for item in index["images"] if item["split"] == "val")
    assert val["boxes"][0]["class_id"] == 0
    saved = client.get(f"/api/v1/training/reviews/{review['id']}").json()
    assert saved["status"] == "published" and saved["publications"][0]["dataset_id"] == new["id"]
    assert (
        client.post(
            "/api/v1/training/reviews/publish",
            json={**payload, "acknowledge_evaluation_reuse": True},
        ).status_code
        == 409
    )
    with pytest.raises(ApplicationError):
        get_container().reviews.get(UUID(review["id"]), uuid4())
