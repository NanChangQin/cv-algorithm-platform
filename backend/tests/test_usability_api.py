import io

from cv_algorithm_sdk import AlgorithmManifest
from fastapi.testclient import TestClient
from PIL import Image

from cv_platform.core.project_context import DEFAULT_PROJECT_ID
from cv_platform.dependencies import get_container
from cv_platform.modules.algorithms.domain.entities import AlgorithmVersion


def upload_png(client: TestClient, size=(800, 400), mode="RGBA") -> dict:
    output = io.BytesIO()
    Image.new(mode, size, (255, 0, 0, 128) if mode == "RGBA" else "red").save(output, format="PNG")
    response = client.post(
        "/api/v1/assets/upload",
        files=[("files", ("wide.png", output.getvalue(), "image/png"))],
    )
    assert response.status_code == 201
    return response.json()["assets"][0]


def test_thumbnail_is_resized_cached_and_bounded(client: TestClient) -> None:
    asset = upload_png(client)

    first = client.get(f"/api/v1/assets/{asset['id']}/thumbnail?size=320")
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/jpeg"
    assert "max-age" in first.headers["cache-control"]
    with Image.open(io.BytesIO(first.content)) as thumbnail:
        assert thumbnail.format == "JPEG"
        assert thumbnail.size == (320, 160)
        # 透明区域铺白底，而不是变成黑色
        assert thumbnail.getpixel((10, 10))[1] > 100

    assert client.get(f"/api/v1/assets/{asset['id']}/thumbnail?size=320").content == first.content
    invalid = client.get(f"/api/v1/assets/{asset['id']}/thumbnail?size=999")
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "THUMBNAIL_SIZE_INVALID"


def test_recent_comparisons_are_listed_newest_first(client: TestClient) -> None:
    asset = upload_png(client, size=(32, 24), mode="RGB")
    classifier = AlgorithmVersion.available(
        AlgorithmManifest.model_validate({
            "id": "demo-classifier",
            "name": "Demo Classifier",
            "version": "1.0.0",
            "task_type": "classification",
            "runtime": {"framework": "onnx", "device": "cpu"},
            "input": {"media_types": ["image/png"]},
            "output": {"type": "classification"},
        }),
        "cv-platform/demo-classifier:1.0.0",
        project_id=DEFAULT_PROJECT_ID,
    )
    get_container().algorithms.add(classifier)
    detector_id = client.get("/api/v1/algorithms").json()[0]["id"]

    created = []
    for _ in range(2):
        response = client.post(
            "/api/v1/comparisons",
            json={"asset_id": asset["id"], "algorithm_version_ids": [detector_id, str(classifier.id)]},
        )
        assert response.status_code == 201
        created.append(response.json()["id"])

    listed = client.get("/api/v1/comparisons").json()
    assert [item["id"] for item in listed] == created[::-1]
    assert {task["algorithm_version_id"] for task in listed[0]["tasks"]} == {detector_id, str(classifier.id)}
    assert len(client.get("/api/v1/comparisons?limit=1").json()) == 1
