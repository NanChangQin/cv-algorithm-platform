from fastapi.testclient import TestClient

from cv_platform.core.config import get_settings
from cv_platform.dependencies import get_container
from cv_platform.main import create_app


def test_authentication_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CV_PLATFORM_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    get_container.cache_clear()

    try:
        with TestClient(create_app()) as client:
            session = client.get("/api/v1/auth/me")
            assert session.status_code == 200
            assert session.json()["username"] == "admin"
            assert session.json()["role"] == "admin"
            assert session.json()["authentication_enabled"] is False
            assert client.get("/api/v1/users").status_code == 200
    finally:
        get_container.cache_clear()
        get_settings.cache_clear()


def test_cross_site_writes_are_rejected_without_login(monkeypatch) -> None:
    from io import BytesIO

    from PIL import Image

    monkeypatch.setenv("CV_PLATFORM_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    get_container.cache_clear()
    image = BytesIO()
    Image.new("RGB", (8, 8), "white").save(image, format="PNG")

    def upload(fetch_site: str):
        return client.post(
            "/api/v1/assets/upload",
            headers={"Sec-Fetch-Site": fetch_site},
            files={"files": ("sample.png", image.getvalue(), "image/png")},
        )

    try:
        with TestClient(create_app()) as client:
            rejected = upload("cross-site")
            assert rejected.status_code == 403
            assert rejected.json()["code"] == "CROSS_SITE_REQUEST"
            assert client.get("/api/v1/assets").json() == []
            assert upload("same-origin").status_code == 201
            assert client.get(
                "/api/v1/assets", headers={"Sec-Fetch-Site": "cross-site"}
            ).status_code == 200
    finally:
        get_container.cache_clear()
        get_settings.cache_clear()
