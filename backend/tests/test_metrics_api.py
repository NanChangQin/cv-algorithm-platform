from fastapi.testclient import TestClient


def test_prometheus_metrics(client: TestClient) -> None:
    client.get("/health")
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "cv_platform_http_requests_total" in response.text
    assert "cv_platform_tasks" in response.text
    assert "cv_platform_average_inference_milliseconds" in response.text
