from fastapi.testclient import TestClient


def test_project_creation_and_isolation(client: TestClient) -> None:
    default_project = client.get("/api/v1/projects").json()[0]
    created = client.post(
        "/api/v1/projects",
        json={"name": "Project B", "description": "isolated"},
    )
    assert created.status_code == 201
    project_b = created.json()

    default_algorithms = client.get(
        "/api/v1/algorithms",
        headers={"X-Project-ID": default_project["id"]},
    )
    project_b_algorithms = client.get(
        "/api/v1/algorithms",
        headers={"X-Project-ID": project_b["id"]},
    )

    assert len(default_algorithms.json()) == 1
    assert project_b_algorithms.json() == []


def test_viewer_cannot_write_project(client: TestClient) -> None:
    user = client.post(
        "/api/v1/users",
        json={"username": "viewer-user", "password": "Viewer-Pass-123!", "role": "user"},
    ).json()
    project = client.get("/api/v1/projects").json()[0]
    member = client.post(
        f"/api/v1/projects/{project['id']}/members",
        json={"username": "viewer-user", "role": "viewer"},
    )
    assert member.status_code == 201
    assert member.json()["user_id"] == user["id"]

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "viewer-user", "password": "Viewer-Pass-123!"},
    )
    assert login.status_code == 200
    denied = client.post(
        "/api/v1/tasks",
        headers={"X-Project-ID": project["id"]},
        json={
            "algorithm_version_id": client.get("/api/v1/algorithms").json()[0]["id"],
            "asset_uri": "file:///data/demo.jpg",
            "parameters": {},
        },
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "PROJECT_READ_ONLY"
