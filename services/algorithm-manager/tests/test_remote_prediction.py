import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from algorithm_manager.api import build_router
from algorithm_manager.application.errors import ManagerError
from algorithm_manager.domain.models import InstanceStatus


class Service:
    def get_instance(self, instance_id):
        assert instance_id == "container-1"
        return SimpleNamespace(id=instance_id, status=InstanceStatus.HEALTHY,
                               endpoint="http://internal-container:8000")

    def touch(self, instance_id):
        return self.get_instance(instance_id)


@pytest.mark.parametrize("timeout", [False, True])
def test_remote_input_is_readable_and_removed(monkeypatch, tmp_path, timeout):
    from algorithm_manager import api
    monkeypatch.setattr(api, "get_settings", lambda: SimpleNamespace(
        data_root=tmp_path, max_input_bytes=1024, prediction_timeout_seconds=5,
    ))
    def predict(url, *, json, timeout):
        assert url == "http://internal-container:8000/predict"
        assert json["request_id"] == "request-1"
        assert json["parameters"] == {"confidence": 0.3}
        local = tmp_path / json["input"]["asset_uri"].removeprefix("file:///data/")
        assert local.read_bytes() == b"image-content"
        # 推理框架按扩展名识别图片
        assert local.suffix == ".png"
        assert local.stat().st_mode & 0o444 == 0o444
        if test_timeout:
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(200, json={"request_id": "request-1"})
    test_timeout = timeout
    monkeypatch.setattr(api.httpx, "post", predict)
    app = FastAPI()
    app.include_router(build_router(lambda: Service()))
    @app.exception_handler(ManagerError)
    async def handle_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code})
    client = TestClient(app)
    response = client.post("/instances/container-1/predict", data={
        "request_id": "request-1", "parameters": json.dumps({"confidence": 0.3}),
    }, files={"file": ("3f2a.png", b"image-content")})
    assert response.status_code == (504 if timeout else 200)
    assert list((tmp_path / "remote-inputs").iterdir()) == []
