import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from algorithm_manager import config
from algorithm_manager.api import EnsureInstanceRequest


def ensure_payload(**overrides):
    payload = {
        "algorithm_version_id": "version-1",
        "algorithm_key": "faster-rcnn",
        "image": "cv-platform/faster-rcnn:dev",
        "container_name": "cv-algorithm-faster-rcnn",
    }
    return {**payload, **overrides}


class FakeService:
    def list_gpus(self):
        return [{"index": 0, "name": "Test GPU"}]


def create_client(monkeypatch, token: str | None) -> TestClient:
    if token is None:
        monkeypatch.delenv("ALGORITHM_MANAGER_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("ALGORITHM_MANAGER_API_TOKEN", token)
    config.get_settings.cache_clear()
    from algorithm_manager import main

    app = main.create_app()
    app.dependency_overrides[main.get_service] = FakeService
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_settings():
    yield
    config.get_settings.cache_clear()


def test_token_protects_everything_except_health(monkeypatch):
    client = create_client(monkeypatch, "node-secret")

    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/system/gpus").status_code == 401
    assert client.get("/docs").status_code == 401
    wrong = client.get("/api/v1/system/gpus", headers={"Authorization": "Bearer other"})
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "MANAGER_AUTH_REQUIRED"
    allowed = client.get("/api/v1/system/gpus", headers={"Authorization": "Bearer node-secret"})
    assert allowed.status_code == 200
    assert allowed.json()[0]["name"] == "Test GPU"


def test_without_token_the_api_stays_open_for_trusted_networks(monkeypatch):
    client = create_client(monkeypatch, None)
    assert client.get("/api/v1/system/gpus").status_code == 200


@pytest.mark.parametrize(
    "image",
    [
        "cv-platform/faster-rcnn:dev",
        "cv-platform/0123456789ab-yolo-detector:1.2.0-rc.1",
    ],
)
def test_algorithm_images_are_accepted(image):
    assert EnsureInstanceRequest(**ensure_payload(image=image)).image == image


@pytest.mark.parametrize(
    "image",
    [
        "postgres:17-alpine",
        "docker.io/cv-platform/faster-rcnn:dev",
        "cv-platform/backend:dev",
        "cv-platform/algorithm-manager:dev",
        "cv-platform/yolo:1.0.0+build.1",
        "cv-platform/Yolo:dev",
    ],
)
def test_other_images_are_rejected(image):
    with pytest.raises(ValidationError):
        EnsureInstanceRequest(**ensure_payload(image=image))


def test_memory_request_is_capped():
    with pytest.raises(ValidationError):
        EnsureInstanceRequest(**ensure_payload(memory_mb=10_000_000))
