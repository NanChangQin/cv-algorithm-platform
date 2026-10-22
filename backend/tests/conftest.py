from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from cv_platform.core.config import get_settings
from cv_platform.core.database import conninfo_from_settings
from cv_platform.dependencies import get_container
from cv_platform.main import create_app


def discard_container() -> None:
    """关闭缓存容器的连接池再清缓存，避免每个测试泄漏一个数据库连接。"""
    if get_container.cache_info().currsize:
        database = get_container().database
        if database is not None:
            database.close()
    get_container.cache_clear()


@pytest.fixture(autouse=True)
def isolated_database() -> Iterator[None]:
    """Postgres 模式（CI 的 postgres 任务）下每个测试从空库开始。"""
    settings = get_settings()
    if settings.persistence_backend == "postgres":
        discard_container()
        with psycopg.connect(conninfo_from_settings(settings), autocommit=True) as connection:
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            ]
            connection.execute(f"TRUNCATE {', '.join(tables)} CASCADE")
    yield
    discard_container()


class RecordingTaskQueue:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.cancelled: list[str] = []

    def submit(self, task_id: str) -> None:
        self.submitted.append(task_id)

    def cancel(self, task_id: str) -> None:
        self.cancelled.append(task_id)

    def shutdown(self) -> None:
        return None


class NeverLeader:
    def held(self) -> bool:
        return False

    def release(self) -> None:
        return None


@pytest.fixture
def client() -> TestClient:
    get_container.cache_clear()
    container = get_container()
    container.task_queue.shutdown()
    container.build_queue.shutdown()
    object.__setattr__(container, "task_queue", RecordingTaskQueue())
    # 测试里手动推进训练协调，后台循环不参与
    object.__setattr__(container, "coordinator_lock", NeverLeader())
    with TestClient(create_app()) as test_client:
        login = test_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "ChangeMe-Local-123!"},
        )
        assert login.status_code == 200
        yield test_client
    get_container.cache_clear()


class FakeNode:
    """模拟训练节点上的 Algorithm Manager。"""

    node_id = "local"
    node_name = "测试服务器"

    def __init__(self) -> None:
        self.datasets: dict[str, str] = {}
        self.uploads = 0
        self.started: list[dict] = []
        self.state: dict = {"state": "running", "progress": None, "metrics": [], "files": {}}
        self.stop_calls = 0
        self.deleted: list[str] = []
        self.has_image = True
        self.gpu_list = [
            {"index": 0, "name": "A", "memory_total_mb": 16000, "memory_used_mb": 9000},
            {"index": 1, "name": "B", "memory_total_mb": 16000, "memory_used_mb": 1000},
        ]

    def gpus(self):
        return self.gpu_list

    def image_exists(self, image):
        return self.has_image

    def upload(self, path, source: Path, *, headers, progress=None):
        self.uploads += 1
        self.datasets[path.rsplit("/", 1)[-1]] = headers["X-Content-SHA256"]
        if progress:
            progress(source.stat().st_size, source.stat().st_size)

    def download(self, path, target: Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        content = f"content of {path.rsplit('/files/', 1)[-1]}".encode()
        target.write_bytes(content)
        return len(content)

    def request(self, method, path, *, json=None, params=None, timeout=None,
                allow_missing=False):
        if method == "GET" and path.startswith("/datasets/"):
            sha = self.datasets.get(path.rsplit("/", 1)[-1])
            return {"sha256": sha} if sha else None
        if method == "POST" and path == "/trainings":
            self.started.append(json)
            return {"state": "running"}
        if method == "POST" and path.endswith("/stop"):
            self.stop_calls += 1
            self.state = {**self.state, "state": "stopped"}
            return self.state
        if method == "GET" and path.endswith("/logs"):
            return ["epoch 1", "epoch 2"]
        if method == "GET" and path.startswith("/trainings/"):
            return self.state
        if method == "DELETE":
            self.deleted.append(path)
            return None
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture
def node(monkeypatch) -> "FakeNode":
    fake = FakeNode()
    cluster = get_container().algorithm_manager
    monkeypatch.setattr(cluster, "node_client", lambda node_id: fake)
    monkeypatch.setattr(cluster, "node_clients", lambda: [fake])
    return fake
