from fastapi.testclient import TestClient


def test_workflow_rejects_cycle(client: TestClient) -> None:
    algorithms = client.get("/api/v1/algorithms").json()
    response = client.post(
        "/api/v1/workflows",
        json={
            "name": "cyclic",
            "mode": "parallel",
            "nodes": [
                {
                    "id": "a",
                    "algorithm_version_id": algorithms[0]["id"],
                    "parameters": {},
                    "depends_on": ["b"],
                },
                {
                    "id": "b",
                    "algorithm_version_id": algorithms[0]["id"],
                    "parameters": {},
                    "depends_on": ["a"],
                },
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "WORKFLOW_CYCLE"


def test_generate_kserve_manifest(client: TestClient) -> None:
    algorithm = client.get("/api/v1/algorithms").json()[0]
    response = client.get(
        f"/api/v1/deployment-manifests/{algorithm['id']}?backend=kserve"
    )

    assert response.status_code == 200
    manifest = response.json()["files"]["inferenceservice.yaml"]
    assert "kind: InferenceService" in manifest
    assert "readOnlyRootFilesystem: true" in manifest
