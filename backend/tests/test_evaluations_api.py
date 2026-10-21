import io
import json
from uuid import UUID

from cv_algorithm_sdk import AlgorithmManifest, AlgorithmResult, TaskStatus
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import TypeAdapter

from cv_platform.core.project_context import DEFAULT_PROJECT_ID
from cv_platform.dependencies import get_container
from cv_platform.modules.algorithms.domain.entities import AlgorithmVersion
from cv_platform.modules.evaluations import service as evaluation_service

RESULT = TypeAdapter(AlgorithmResult)


def upload(client: TestClient, *names: str) -> list[dict]:
    files = []
    for index, name in enumerate(names):
        image = io.BytesIO()
        Image.new("RGB", (100, 100), (index * 40, 10, 10)).save(image, format="PNG")
        files.append(("files", (name, image.getvalue(), "image/png")))
    response = client.post("/api/v1/assets/upload", files=files)
    assert response.status_code == 201
    return response.json()["assets"]


def coco(*entries: tuple[str, list[tuple[str, list[float]]]]) -> str:
    images, annotations, categories = [], [], {}
    for image_id, (file_name, boxes) in enumerate(entries, start=1):
        images.append({"id": image_id, "file_name": file_name, "width": 100, "height": 100})
        for label, bbox in boxes:
            category = categories.setdefault(label, len(categories) + 1)
            annotations.append({"id": len(annotations) + 1, "image_id": image_id,
                                "category_id": category, "bbox": bbox})
    return json.dumps({
        "images": images, "annotations": annotations,
        "categories": [{"id": value, "name": key} for key, value in categories.items()],
    })


def create_dataset(client: TestClient, assets: list[dict], annotations: str, **overrides) -> dict:
    payload = {
        "name": "街景检测", "task_type": "object_detection", "annotations_filename": "coco.json",
        "annotations": annotations, "asset_ids": [asset["id"] for asset in assets], **overrides,
    }
    return client.post("/api/v1/evaluations/datasets", json=payload).json()


def detector_id(client: TestClient) -> str:
    return client.get("/api/v1/algorithms").json()[0]["id"]


def complete(task_id: str, detections: list[dict]) -> None:
    tasks = get_container().tasks
    task = tasks.get(UUID(task_id))
    result = RESULT.validate_python({
        "schema_version": "1.0", "request_id": task_id, "type": "object_detection",
        "algorithm": {"id": "faster-rcnn-resnet50", "version": "1.0.0"},
        "input": {"width": 100, "height": 100},
        "timing": {"preprocess_ms": 1, "inference_ms": 5, "postprocess_ms": 1},
        "data": {"detections": detections},
    })
    running = task.transition(TaskStatus.RUNNING)
    tasks.save(running)
    tasks.save(running.transition(TaskStatus.COMPLETED, result=result))


def test_coco_annotations_are_matched_to_uploaded_images_by_file_name(client: TestClient) -> None:
    assets = upload(client, "a.png", "b.png")
    created = create_dataset(client, assets, coco(
        ("train/a.png", [("person", [10, 10, 30, 40])]),
        ("c.png", [("car", [0, 0, 10, 10])]),
    ))
    assert created["item_count"] == 1
    assert created["unmatched_files"] == ["c.png"]
    assert created["unlabeled_images"] == ["b.png"]
    listed = client.get("/api/v1/evaluations/datasets").json()
    assert [item["name"] for item in listed] == ["街景检测"]


def test_csv_labels_create_a_classification_dataset(client: TestClient) -> None:
    assets = upload(client, "cat.png", "dog.png")
    created = create_dataset(
        client, assets, "文件名,标签\ncat.png,cat\nDOG.png,dog\n",
        task_type="classification", annotations_filename="labels.csv", name="猫狗",
    )
    assert created["item_count"] == 2
    assert created["unmatched_files"] == []


def test_unusable_annotations_are_explained(client: TestClient) -> None:
    assets = upload(client, "a.png")
    broken = client.post("/api/v1/evaluations/datasets", json={
        "name": "x", "task_type": "object_detection", "annotations_filename": "a.json",
        "annotations": "{not json", "asset_ids": [assets[0]["id"]],
    })
    assert broken.status_code == 400
    assert broken.json()["code"] == "ANNOTATIONS_INVALID"
    nothing = client.post("/api/v1/evaluations/datasets", json={
        "name": "x", "task_type": "object_detection", "annotations_filename": "a.json",
        "annotations": coco(("other.png", [])), "asset_ids": [assets[0]["id"]],
    })
    assert nothing.status_code == 400
    assert nothing.json()["code"] == "DATASET_EMPTY"


def test_run_scores_each_algorithm_and_caches_the_finished_report(client: TestClient) -> None:
    assets = upload(client, "a.png", "b.png")
    dataset = create_dataset(client, assets, coco(
        ("a.png", [("person", [10, 10, 50, 50])]),
        ("b.png", [("person", [0, 0, 20, 20]), ("car", [60, 60, 90, 90])]),
    ))
    algorithm = detector_id(client)
    started = client.post("/api/v1/evaluations/runs", json={
        "dataset_id": dataset["id"], "algorithm_version_ids": [algorithm],
    })
    assert started.status_code == 201
    run = started.json()
    assert run["task_count"] == 2 and run["finished"] is False

    pending = client.get(f"/api/v1/evaluations/runs/{run['id']}").json()
    assert pending["report"]["finished"] is False
    assert pending["report"]["algorithms"][0]["active"] == 2

    stored = get_container().evaluations.get_run(UUID(run["id"]))
    tasks = {key.split(":")[1]: value for key, value in stored.task_ids.items()}
    complete(tasks[assets[0]["id"]], [{"label": "person", "score": 0.9, "bbox": [11, 11, 50, 50]}])
    complete(tasks[assets[1]["id"]], [{"label": "dog", "score": 0.8, "bbox": [0, 0, 20, 20]}])

    report = client.get(f"/api/v1/evaluations/runs/{run['id']}").json()
    assert report["run"]["finished"] is True
    summary = report["report"]["algorithms"][0]
    assert summary["completed"] == 2 and summary["failed"] == 0
    # 累计 TP=1、FP=1、FN=2：精确率 1/2，召回率 1/3
    assert summary["metrics"]["precision_iou50"] == 0.5
    assert round(summary["metrics"]["recall_iou50"], 4) == 0.3333
    assert [item["asset_id"] for item in summary["worst"]] == [assets[1]["id"], assets[0]["id"]]
    assert get_container().evaluations.get_run(UUID(run["id"])).report == report["report"]
    assert client.get("/api/v1/evaluations/runs").json()[0]["finished"] is True


def test_run_rejects_algorithms_of_another_task_type_without_creating_tasks(
    client: TestClient,
) -> None:
    assets = upload(client, "a.png")
    dataset = create_dataset(client, assets, coco(("a.png", [("person", [1, 1, 5, 5])])))
    classifier = AlgorithmVersion.available(
        AlgorithmManifest.model_validate({
            "id": "demo-classifier", "name": "分类器", "version": "1.0.0",
            "task_type": "classification", "runtime": {"framework": "onnx", "device": "cpu"},
            "input": {"media_types": ["image/png"]}, "output": {"type": "classification"},
        }),
        "cv-platform/demo-classifier:1.0.0",
        project_id=DEFAULT_PROJECT_ID,
    )
    get_container().algorithms.add(classifier)
    response = client.post("/api/v1/evaluations/runs", json={
        "dataset_id": dataset["id"],
        "algorithm_version_ids": [detector_id(client), str(classifier.id)],
    })
    assert response.status_code == 400
    assert response.json()["code"] == "EVALUATION_INVALID"
    assert client.get("/api/v1/tasks").json() == []


def test_run_size_is_limited(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(evaluation_service, "MAX_RUN_TASKS", 1)
    assets = upload(client, "a.png", "b.png")
    dataset = create_dataset(client, assets, coco(("a.png", []), ("b.png", [])))
    response = client.post("/api/v1/evaluations/runs", json={
        "dataset_id": dataset["id"], "algorithm_version_ids": [detector_id(client)],
    })
    assert response.status_code == 400
    assert response.json()["code"] == "EVALUATION_TOO_LARGE"
