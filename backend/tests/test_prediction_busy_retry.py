from dataclasses import replace

import httpx
import pytest
from test_remote_prediction_gateway import instance

from cv_platform.core.errors import ApplicationError
from cv_platform.modules.tasks.infrastructure import http_prediction_gateway as gateway


@pytest.mark.parametrize("remote", [False, True])
def test_busy_instance_retries_with_same_input_and_request(monkeypatch, tmp_path, remote):
    (tmp_path / "image.jpg").write_bytes(b"image")
    clock = [0.0]
    calls = []

    def post(url, **kwargs):
        if remote:
            assert kwargs["files"]["file"][1].read() == b"image"
            assert kwargs["data"]["request_id"] == "test"
        else:
            assert kwargs["json"]["request_id"] == "test"
        calls.append(kwargs["timeout"])
        if len(calls) < 3:
            return httpx.Response(429, json={"detail": "busy"})
        return httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "request_id": "test",
                "type": "object_detection",
                "algorithm": {"id": "example", "version": "1.0.0"},
                "input": {"width": 10, "height": 10},
                "timing": {"preprocess_ms": 0, "inference_ms": 1, "postprocess_ms": 0},
                "data": {"detections": []},
            },
        )

    monkeypatch.setattr(gateway.httpx, "post", post)
    monkeypatch.setattr(gateway.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(gateway.time, "sleep", lambda value: clock.__setitem__(0, clock[0] + value))
    target = instance() if remote else replace(instance(), node_id="local")
    result = gateway.HttpAlgorithmPredictionGateway(5, tmp_path).predict(
        target, "test", "file:///data/image.jpg", {}
    )
    assert result.request_id == "test" and len(calls) == 3
    assert calls[0] > calls[1] > calls[2]


def test_busy_wait_has_deadline_and_server_errors_are_not_retried(monkeypatch):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(gateway.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(gateway.time, "sleep", lambda value: clock.__setitem__(0, clock[0] + value))

    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(429, json={"detail": "busy"})

    monkeypatch.setattr(gateway.httpx, "post", post)
    target = replace(instance(), node_id="local")
    with pytest.raises(ApplicationError) as error:
        gateway.HttpAlgorithmPredictionGateway(1).predict(
            target, "test", "file:///data/image.jpg", {}
        )
    assert error.value.code == "ALGORITHM_BUSY" and len(calls) == 3 and clock[0] < 1
    calls.clear()

    def failed(*args, **kwargs):
        calls.append(1)
        return httpx.Response(500, json={"message": "failed"})

    monkeypatch.setattr(gateway.httpx, "post", failed)
    with pytest.raises(ApplicationError):
        gateway.HttpAlgorithmPredictionGateway(1).predict(
            target, "test", "file:///data/image.jpg", {}
        )
    assert len(calls) == 1
