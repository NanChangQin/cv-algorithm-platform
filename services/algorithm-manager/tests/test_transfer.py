import hashlib
import io
import json
import tarfile
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from algorithm_manager.api import validate_algorithm_image
from algorithm_manager.application.errors import ManagerError
from algorithm_manager.transfer import TransferService, build_transfer_router


class FakeImages:
    def __init__(self):
        self.present = {"cv-platform/yolo:dev"}
        self.loaded = []

    def get(self, image):
        if image not in self.present:
            raise RuntimeError("missing")
        return SimpleNamespace(
            id=f"sha256:{image}", save=lambda chunk_size, named: iter([b"tar-", b"bytes"])
        )

    def load(self, stream):
        self.loaded.append(stream.read())
        self.present.add("cv-platform/faster-rcnn:dev")


@pytest.fixture
def images():
    return FakeImages()


@pytest.fixture
def roots(tmp_path):
    return SimpleNamespace(
        data=tmp_path / "data", packages=tmp_path / "packages", models=tmp_path / "models"
    )


@pytest.fixture
def client(images, roots, monkeypatch):
    monkeypatch.setattr(
        "algorithm_manager.api.get_settings",
        lambda: SimpleNamespace(allowed_image_prefixes=["cv-platform/"]),
    )
    service = TransferService(
        SimpleNamespace(images=images), roots.data, roots.packages, roots.models,
        validate_algorithm_image,
    )
    app = FastAPI()
    app.include_router(build_transfer_router(lambda: service, max_image_bytes=1_000_000))

    @app.exception_handler(ManagerError)
    async def handle(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code})

    return TestClient(app)


def image_tar(tags):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        content = json.dumps([{"Config": "c.json", "RepoTags": tags, "Layers": []}]).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_images_are_exported_as_a_stream(client):
    response = client.get("/images/export", params={"image": "cv-platform/yolo:dev"})
    assert response.content == b"tar-bytes"
    missing = client.get("/images/export", params={"image": "cv-platform/other:dev"})
    assert missing.json()["code"] == "IMAGE_MISSING"
    assert client.get("/images/export", params={"image": "postgres:17"}).status_code == 400


def test_only_the_requested_algorithm_image_can_be_imported(client, images, roots):
    image = "cv-platform/faster-rcnn:dev"
    response = client.put("/images/import", params={"image": image}, content=image_tar([image]))
    assert response.status_code == 200
    assert response.json()["exists"] is True
    assert len(images.loaded) == 1

    # 镜像文件里夹带了别的标签（例如覆盖平台自身的服务镜像）
    sneaky = image_tar([image, "cv-platform/backend:dev"])
    assert client.put("/images/import", params={"image": image}, content=sneaky).json()["code"] \
        == "IMAGE_INVALID"
    service = "cv-platform/backend:dev"
    assert client.put(
        "/images/import", params={"image": service}, content=image_tar([service])
    ).status_code == 400
    assert client.put("/images/import", params={"image": image}, content=b"not a tar").json()[
        "code"] == "IMAGE_INVALID"
    assert len(images.loaded) == 1
    assert list((roots.data / "transfers").iterdir()) == []


def test_packages_are_unpacked_inside_the_package_root(client, roots):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.yaml", "id: demo")
        archive.writestr("src/service.py", "app = None")
    response = client.put("/packages/0123abcd", content=buffer.getvalue())
    path = roots.packages / "remote" / "0123abcd"
    assert response.json() == {"package_path": str(path)}
    assert (path / "src/service.py").read_text() == "app = None"

    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("../escape.py", "x")
    assert client.put("/packages/0123abcd", content=evil.getvalue()).json()["code"] \
        == "PACKAGE_INVALID"
    assert (path / "src/service.py").exists()
    assert client.put("/packages/..", content=buffer.getvalue()).status_code in {400, 404, 405}


def test_model_files_are_verified_listed_and_served(client, roots):
    content = b"weights" * 100
    sha = hashlib.sha256(content).hexdigest()
    url = "/models/trained/abc123/files/model.pt"
    assert client.put(url, content=content, headers={"X-Content-SHA256": "0" * 64}).json()[
        "code"] == "MODEL_FILE_INVALID"
    assert client.get("/models/trained/abc123").json()["files"] == {}

    assert client.put(url, content=content, headers={"X-Content-SHA256": sha}).status_code == 200
    listing = client.get("/models/trained/abc123").json()["files"]
    assert listing == {"model.pt": {"size": len(content), "sha256": sha}}
    assert client.get(url).content == content
    stored = roots.models / "trained/abc123/model.pt"
    assert stored.stat().st_mode & 0o444 == 0o444
    assert (roots.models / "trained").stat().st_mode & 0o555 == 0o555
    assert list((roots.models / ".incoming").iterdir()) == []


@pytest.mark.parametrize("path", [
    "/models/..%2Fetc/files/passwd",
    "/models/a/files/..%2F..%2Fpasswd",
    "/models/a/b/c/d/files/x.pt",
])
def test_model_paths_cannot_escape(client, path):
    assert client.get(path).status_code in {400, 404}
