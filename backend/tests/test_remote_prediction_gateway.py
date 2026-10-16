from datetime import UTC, datetime

import httpx
import pytest

from cv_platform.core.errors import ApplicationError
from cv_platform.modules.instances.domain.models import RuntimeInstance
from cv_platform.modules.tasks.infrastructure.http_prediction_gateway import HttpAlgorithmPredictionGateway


def instance():
    now = datetime.now(UTC)
    return RuntimeInstance("container", "algorithm", "image", "name",
                           "http://remote:8010/api/v1/instances/container", "healthy", "gpu",
                           now, now, node_id="remote", gpu_device_ids=("0",))


def test_remote_gateway_streams_the_platform_input(monkeypatch, tmp_path):
    source = tmp_path / "assets" / "image.jpg"
    source.parent.mkdir()
    source.write_bytes(b"input-image")
    def post(url, *, data, files, timeout):
        assert url == "http://remote:8010/api/v1/instances/container/predict"
        assert data["request_id"] == "request-1"
        assert files["file"][1].read() == b"input-image"
        return httpx.Response(200, json={
            "schema_version": "1.0", "request_id": "request-1", "type": "object_detection",
            "algorithm": {"id": "example", "version": "1.0.0"},
            "input": {"width": 10, "height": 10},
            "timing": {"preprocess_ms": 0, "inference_ms": 1, "postprocess_ms": 0},
            "data": {"detections": []},
        })
    monkeypatch.setattr(httpx, "post", post)
    result = HttpAlgorithmPredictionGateway(5, tmp_path).predict(
        instance(), "request-1", "file:///data/assets/image.jpg", {},
    )
    assert result.request_id == "request-1"


@pytest.mark.parametrize("uri", ["file:///data/../secret", "file:///etc/passwd", "file://host/data/a"])
def test_remote_input_cannot_escape_platform_storage(tmp_path, uri):
    with pytest.raises(ApplicationError) as error:
        HttpAlgorithmPredictionGateway(5, tmp_path)._resolve_input(uri)
    assert error.value.code == "INPUT_INVALID"
