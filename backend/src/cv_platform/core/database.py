from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import Settings


def conninfo_from_settings(settings: Settings) -> str:
    conninfo = settings.database_url
    if conninfo and conninfo.startswith("postgresql+psycopg://"):
        conninfo = "postgresql://" + conninfo.removeprefix("postgresql+psycopg://")
    if conninfo:
        return conninfo
    if not settings.postgres_password:
        raise RuntimeError("CV_PLATFORM_POSTGRES_PASSWORD is required")
    return make_conninfo(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )


class Database:
    """Pooled connections for the synchronous FastAPI repository adapters.

    每次新建连接（TCP + 认证）要几十毫秒，一个请求往往要查好几次，所以复用连接。
    connect() 的语义不变：代码块正常结束时提交，抛异常时回滚。
    """

    def __init__(self, conninfo: str, *, max_size: int = 10, timeout: float = 10) -> None:
        self._conninfo = conninfo
        self._pool: ConnectionPool[Connection[dict[str, object]]] = ConnectionPool(
            conninfo,
            kwargs={"row_factory": dict_row, "connect_timeout": 5},
            min_size=1,
            max_size=max_size,
            timeout=timeout,
            # 数据库重启后池里的旧连接已失效，取出时先检查，坏的直接丢弃重连
            check=ConnectionPool.check_connection,
            name="cv-platform",
            open=True,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        return cls(conninfo_from_settings(settings), max_size=settings.database_pool_size)

    @contextmanager
    def connect(self) -> Iterator[Connection[dict[str, object]]]:
        with self._pool.connection() as connection:
            yield connection

    @contextmanager
    def dedicated(self) -> Iterator[Connection[dict[str, object]]]:
        """不经过连接池的自动提交连接，退出时关闭。

        会话级状态（例如 advisory lock）必须用它：池里的连接归还后会话还在，锁也不会释放。
        """
        with psycopg.connect(
            self._conninfo, row_factory=dict_row, connect_timeout=5, autocommit=True
        ) as connection:
            yield connection

    def is_healthy(self) -> bool:
        try:
            with self._pool.connection(timeout=5) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() is not None
        except psycopg.Error:
            return False

    def close(self) -> None:
        self._pool.close()
