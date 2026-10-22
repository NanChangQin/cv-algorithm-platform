from collections.abc import Iterator

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


@pytest.fixture
def client() -> TestClient:
    get_container.cache_clear()
    container = get_container()
    container.task_queue.shutdown()
    container.build_queue.shutdown()
    object.__setattr__(container, "task_queue", RecordingTaskQueue())
    with TestClient(create_app()) as test_client:
        login = test_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "ChangeMe-Local-123!"},
        )
        assert login.status_code == 200
        yield test_client
    get_container.cache_clear()
