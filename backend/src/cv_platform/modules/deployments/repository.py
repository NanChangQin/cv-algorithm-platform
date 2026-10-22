from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ...core.database import Database
from .domain import ACTIVE_STATUSES, Deployment, DeploymentStatus

# 状态变化都走带预期状态的条件更新，避免后台部署和用户的停止、删除操作互相覆盖
UPDATABLE = {"status", "steps", "instance_id", "error", "started_at"}


class InMemoryDeploymentRepository:
    def __init__(self) -> None:
        self._items: dict[UUID, Deployment] = {}
        self._lock = RLock()

    def add(self, deployment: Deployment) -> None:
        with self._lock:
            self._items[deployment.id] = deployment

    def get(self, deployment_id: UUID) -> Deployment | None:
        with self._lock:
            return self._items.get(deployment_id)

    def list(self, project_id: UUID) -> list[Deployment]:
        with self._lock:
            items = [item for item in self._items.values() if item.project_id == project_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def list_active(self) -> list[Deployment]:
        with self._lock:
            return [item for item in self._items.values() if item.status in ACTIVE_STATUSES]

    def list_for_version(self, algorithm_version_id: UUID) -> list[Deployment]:
        with self._lock:
            return [
                item for item in self._items.values()
                if item.algorithm_version_id == algorithm_version_id
            ]

    def update(
        self,
        deployment_id: UUID,
        expected: Iterable[DeploymentStatus] | None = None,
        **fields: Any,
    ) -> bool:
        _check_fields(fields)
        with self._lock:
            current = self._items.get(deployment_id)
            if current is None or (expected is not None and current.status not in set(expected)):
                return False
            self._items[deployment_id] = replace(current, **fields, updated_at=datetime.now(UTC))
            return True

    def delete(self, deployment_id: UUID) -> None:
        with self._lock:
            self._items.pop(deployment_id, None)


class PostgresDeploymentRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    @staticmethod
    def _row(row: dict[str, Any]) -> Deployment:
        return Deployment(
            id=row["id"],
            project_id=row["project_id"],
            algorithm_version_id=row["algorithm_version_id"],
            node_id=str(row["node_id"]),
            gpu_device_ids=tuple(row["gpu_device_ids"]),
            status=DeploymentStatus(row["status"]),
            created_by=str(row["created_by"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            steps=tuple(row["steps"]),
            instance_id=row["instance_id"],
            error=row["error"],
            started_at=row["started_at"],
        )

    def add(self, deployment: Deployment) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO deployments (
                    id, project_id, algorithm_version_id, node_id, gpu_device_ids, status,
                    steps, created_by, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    deployment.id, deployment.project_id, deployment.algorithm_version_id,
                    deployment.node_id, Jsonb(list(deployment.gpu_device_ids)),
                    deployment.status.value, Jsonb(list(deployment.steps)),
                    deployment.created_by, deployment.created_at, deployment.updated_at,
                ),
            )

    def get(self, deployment_id: UUID) -> Deployment | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM deployments WHERE id = %s", (deployment_id,))
            row = cursor.fetchone()
            return self._row(row) if row else None

    def list(self, project_id: UUID) -> list[Deployment]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM deployments WHERE project_id = %s ORDER BY created_at DESC",
                (project_id,),
            )
            return [self._row(row) for row in cursor.fetchall()]

    def list_active(self) -> list[Deployment]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM deployments WHERE status = ANY(%s) ORDER BY created_at",
                ([status.value for status in ACTIVE_STATUSES],),
            )
            return [self._row(row) for row in cursor.fetchall()]

    def list_for_version(self, algorithm_version_id: UUID) -> list[Deployment]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM deployments WHERE algorithm_version_id = %s",
                (algorithm_version_id,),
            )
            return [self._row(row) for row in cursor.fetchall()]

    def update(
        self,
        deployment_id: UUID,
        expected: Iterable[DeploymentStatus] | None = None,
        **fields: Any,
    ) -> bool:
        _check_fields(fields)
        assignments = ", ".join(f"{name} = %s" for name in fields)
        values = [
            value.value if isinstance(value, DeploymentStatus)
            else Jsonb(list(value)) if name == "steps" else value
            for name, value in fields.items()
        ]
        condition = " AND status = ANY(%s)" if expected is not None else ""
        extra = [[status.value for status in expected]] if expected is not None else []
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE deployments SET {assignments}, updated_at = now() "
                f"WHERE id = %s{condition}",
                (*values, deployment_id, *extra),
            )
            return cursor.rowcount > 0

    def delete(self, deployment_id: UUID) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM deployments WHERE id = %s", (deployment_id,))


def _check_fields(fields: dict[str, Any]) -> None:
    unknown = set(fields) - UPDATABLE
    if unknown or not fields:
        raise ValueError(f"cannot update deployment fields: {sorted(unknown)}")
