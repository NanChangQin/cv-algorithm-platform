import io
from uuid import UUID

from cv_algorithm_sdk import AlgorithmManifest
from fastapi.testclient import TestClient
from PIL import Image

from cv_platform.core.config import get_settings
from cv_platform.core.project_context import DEFAULT_PROJECT_ID
from cv_platform.dependencies import get_container
from cv_platform.main import create_app
from cv_platform.modules.algorithms.domain.entities import AlgorithmVersion
from cv_platform.modules.assets.application.service import AssetService
from cv_platform.modules.assets.infrastructure.local_storage import LocalAssetStorage
from cv_platform.modules.assets.infrastructure.memory_repository import InMemoryAssetRepository


def png_bytes(color="red") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(output, format="PNG")
    return output.getvalue()


def upload(client: TestClient) -> dict:
    files = [("files", ("a.png", png_bytes(), "image/png"))]
    response = client.post("/api/v1/assets/upload", files=files)
    assert response.status_code == 201
    return response.json()["assets"][0]


def detector_id(client: TestClient) -> str:
    return client.get("/api/v1/algorithms").json()[0]["id"]


def test_asset_uri_must_belong_to_the_project(client: TestClient) -> None:
    asset = upload(client)
    stored = get_container().assets.get(UUID(asset["id"]))

    own = client.post(
        "/api/v1/tasks",
        json={"algorithm_version_id": detector_id(client), "asset_uri": stored.algorithm_uri},
    )
    assert own.status_code == 201
    assert own.json()["asset_id"] == asset["id"]

    foreign = client.post(
        "/api/v1/tasks",
        json={"algorithm_version_id": detector_id(client), "asset_uri": "file:///data/assets/ff/other-project.jpg"},
    )
    assert foreign.status_code == 404
    assert foreign.json()["code"] == "ASSET_NOT_FOUND"


def test_invalid_comparison_creates_no_tasks(client: TestClient) -> None:
    asset = upload(client)
    classifier = AlgorithmVersion.available(
        AlgorithmManifest.model_validate({
            "id": "demo-classifier", "name": "Demo", "version": "1.0.0",
            "task_type": "classification", "runtime": {"framework": "onnx", "device": "cpu"},
            "input": {"media_types": ["image/png"]}, "output": {"type": "classification"},
        }),
        "cv-platform/demo-classifier:1.0.0",
        project_id=DEFAULT_PROJECT_ID,
    )
    get_container().algorithms.add(classifier)
    before = len(client.get("/api/v1/tasks").json())

    response = client.post("/api/v1/comparisons", json={
        "asset_id": asset["id"],
        "algorithm_version_ids": [detector_id(client), str(classifier.id)],
        "parameters": {str(classifier.id): {"confidence": 0.5}},
    })

    assert response.status_code == 400
    assert response.json()["code"] == "PARAMETER_INVALID"
    assert len(client.get("/api/v1/tasks").json()) == before


def test_registered_frames_are_removed_from_the_extraction_directory(tmp_path) -> None:
    storage = LocalAssetStorage(tmp_path, "/data", 10 * 1024 * 1024, 10_000_000)
    frame = tmp_path / "media-frames" / "source" / "extraction" / "00000000.jpg"
    frame.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "blue").save(frame, format="JPEG")
    service = AssetService(InMemoryAssetRepository(), storage)

    asset = service.register_local_image(
        "media-frames/source/extraction/00000000.jpg", "frame-00000000.jpg", remove_source=True,
    )

    assert storage.resolve(asset.storage_key).is_file()
    assert not frame.exists()
    assert not (tmp_path / "media-frames" / "source").exists()
    assert (tmp_path / "media-frames").is_dir()


def test_rate_limit_is_per_client_when_everyone_is_admin(monkeypatch) -> None:
    monkeypatch.setenv("CV_PLATFORM_AUTH_ENABLED", "false")
    monkeypatch.setenv("CV_PLATFORM_RATE_LIMIT_REQUESTS", "2")
    get_settings.cache_clear()
    get_container.cache_clear()
    try:
        app = create_app()
        with TestClient(app, client=("10.0.0.1", 50000)) as first:
            second = TestClient(app, client=("10.0.0.2", 50000))
            codes = [first.post("/api/v1/comparisons", json={}).status_code for _ in range(3)]
            assert codes[-1] == 429 and 429 not in codes[:2]
            assert second.post("/api/v1/comparisons", json={}).status_code != 429
    finally:
        get_container.cache_clear()
        get_settings.cache_clear()


def test_api_docs_are_hidden_unless_debug(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
