import io
from uuid import UUID

from cv_algorithm_sdk import AlgorithmResult, TaskStatus
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import TypeAdapter

from cv_platform.dependencies import get_container


def upload(client: TestClient, name: str, color: str) -> dict:
    image = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(image, format="PNG")
    files = [("files", (name, image.getvalue(), "image/png"))]
    response = client.post("/api/v1/assets/upload", files=files)
    assert response.status_code == 201
    return response.json()["assets"][0]


def create_task(client: TestClient, asset: dict) -> dict:
    algorithm = client.get("/api/v1/algorithms").json()[0]
    response = client.post(
        "/api/v1/tasks", json={"algorithm_version_id": algorithm["id"], "asset_id": asset["id"]}
    )
    assert response.status_code == 201
    return response.json()


def test_assets_are_paged_and_searchable(client: TestClient) -> None:
    uploaded = [
        upload(client, name, color)
        for name, color in [
            ("street-1.png", "red"), ("street-2.png", "green"),
            ("street-3.png", "blue"), ("park.png", "white"),
        ]
    ]

    first = client.get("/api/v1/assets", params={"limit": 3})
    second = client.get("/api/v1/assets", params={"limit": 3, "offset": 3})
    assert first.headers["X-Total-Count"] == second.headers["X-Total-Count"] == "4"
    assert [item["id"] for item in first.json() + second.json()] == [
        item["id"] for item in reversed(uploaded)
    ]

    search = client.get("/api/v1/assets", params={"q": "STREET"})
    assert search.headers["X-Total-Count"] == "3"
    assert {item["original_name"] for item in search.json()} == {
        "street-1.png", "street-2.png", "street-3.png",
    }

    picked = client.get(
        "/api/v1/assets", params=[("ids", uploaded[0]["id"]), ("ids", uploaded[3]["id"])]
    )
    assert {item["id"] for item in picked.json()} == {uploaded[0]["id"], uploaded[3]["id"]}

    by_name = client.get("/api/v1/assets", params={"sort": "name", "limit": 2})
    assert [item["original_name"] for item in by_name.json()] == ["park.png", "street-1.png"]

    # 不传 limit 时与旧接口一致，返回全部
    assert len(client.get("/api/v1/assets").json()) == 4


def test_duplicate_upload_is_found_through_the_project_index(client: TestClient) -> None:
    first = upload(client, "a.png", "red")
    again = upload(client, "renamed.png", "red")
    assert again["id"] == first["id"]
    assert client.get("/api/v1/assets").headers["X-Total-Count"] == "1"


def test_tasks_are_paged_filtered_searched_and_summarised(client: TestClient) -> None:
    street = upload(client, "street.png", "red")
    park = upload(client, "park.png", "blue")
    created = [create_task(client, asset) for asset in (street, park, park)]
    client.post(f"/api/v1/tasks/{created[0]['id']}/cancel")

    page = client.get("/api/v1/tasks", params={"limit": 2})
    rest = client.get("/api/v1/tasks", params={"limit": 2, "offset": 2})
    assert page.headers["X-Total-Count"] == "3"
    assert [item["id"] for item in page.json() + rest.json()] == [
        item["id"] for item in reversed(created)
    ]

    active = client.get("/api/v1/tasks", params=[("status", "queued"), ("status", "running")])
    assert active.headers["X-Total-Count"] == "2"
    single = client.get("/api/v1/tasks", params={"status": "cancelled"})
    assert [item["id"] for item in single.json()] == [created[0]["id"]]

    by_image = client.get("/api/v1/tasks", params={"q": "PARK"})
    assert {item["id"] for item in by_image.json()} == {created[1]["id"], created[2]["id"]}
    by_algorithm = client.get("/api/v1/tasks", params={"q": "faster r-cnn"})
    assert by_algorithm.headers["X-Total-Count"] == "3"
    by_id = client.get("/api/v1/tasks", params={"q": created[2]["id"][:13]})
    assert [item["id"] for item in by_id.json()] == [created[2]["id"]]
    assert client.get("/api/v1/tasks", params={"q": "100%_"}).json() == []

    polled = client.get(
        "/api/v1/tasks", params=[("ids", created[1]["id"]), ("ids", created[2]["id"])]
    )
    assert {item["id"] for item in polled.json()} == {created[1]["id"], created[2]["id"]}

    summary = client.get("/api/v1/tasks/summary").json()
    assert summary["total"] == 3
    assert summary["by_status"]["queued"] == 2
    assert summary["by_status"]["cancelled"] == 1
    assert summary["by_status"]["completed"] == 0


def test_aggregates_are_computed_by_the_repository(client: TestClient) -> None:
    asset = upload(client, "a.png", "red")
    created = create_task(client, asset)
    container = get_container()
    algorithm_id = UUID(created["algorithm_version_id"])
    task = container.tasks.get(UUID(created["id"]))
    result = TypeAdapter(AlgorithmResult).validate_python({
        "schema_version": "1.0", "request_id": "stats", "type": "object_detection",
        "algorithm": {"id": "faster-rcnn-resnet50", "version": "1.0.0"},
        "input": {"width": 8, "height": 8},
        "timing": {"preprocess_ms": 1, "inference_ms": 40, "postprocess_ms": 1},
        "data": {"detections": []},
    })
    running = task.transition(TaskStatus.RUNNING)
    container.tasks.save(running)
    container.tasks.save(running.transition(TaskStatus.COMPLETED, result=result))
    create_task(client, asset)

    active = (TaskStatus.QUEUED, TaskStatus.RUNNING)
    assert container.tasks.count_by_algorithm(active) == {algorithm_id: 1}
    assert container.tasks.inference_time_stats(task.project_id) == (40.0, 1)
    assert algorithm_id in container.tasks.last_created_by_algorithm(task.project_id)

    algorithm = client.get("/api/v1/algorithms").json()[0]
    assert algorithm["last_called_at"] is not None
    metrics = client.get("/api/v1/operations/metrics").json()
    assert metrics["tasks_by_status"] == {"completed": 1, "queued": 1}
    assert metrics["average_inference_ms"] == 40.0
    assert metrics["completed_samples"] == 1
