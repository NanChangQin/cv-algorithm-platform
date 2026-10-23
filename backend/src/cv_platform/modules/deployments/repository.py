from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ...core.database import Database
from .domain import ACTIVE_STATUSES, Deployment, DeploymentRoute, DeploymentStatus

# 状态变化都走带预期状态的条件更新，避免后台部署和用户的停止、删除操作互相覆盖
UPDATABLE = {"status", "steps", "instance_id", "error", "started_at", "readiness"}
ROUTE_UPDATABLE = {
    "name",
    "active_deployment_id",
    "previous_deployment_id",
    "candidate_deployment_id",
    "drain_timeout_seconds",
    "last_error",
    "updated_by",
}


class InMemoryDeploymentRepository:
    def __init__(self) -> None:
        self._items: dict[UUID, Deployment] = {}
        self._routes: dict[tuple[UUID, str], DeploymentRoute] = {}
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
            for key, route in list(self._routes.items()):
                updates: dict[str, Any] = {}
                if route.previous_deployment_id == deployment_id:
                    updates["previous_deployment_id"] = None
                if route.candidate_deployment_id == deployment_id:
                    updates["candidate_deployment_id"] = None
                if updates:
                    self._routes[key] = replace(route, **updates, updated_at=datetime.now(UTC))

    # 服务路由 --------------------------------------------------------------

    def add_route(self, route: DeploymentRoute) -> None:
        with self._lock:
            self._routes[(route.project_id, route.key)] = route

    def get_route(self, project_id: UUID, key: str) -> DeploymentRoute | None:
        with self._lock:
            return self._routes.get((project_id, key))

    def list_routes(self, project_id: UUID) -> list[DeploymentRoute]:
        with self._lock:
            items = [
                item for (pid, _), item in self._routes.items()
                if pid == project_id
            ]
        return sorted(items, key=lambda item: item.key)

    def list_pending_routes(self) -> list[DeploymentRoute]:
        with self._lock:
            return [
                item for item in self._routes.values()
                if item.candidate_deployment_id is not None
            ]

    def list_routes_for_deployment(self, deployment_id: UUID) -> list[DeploymentRoute]:
        with self._lock:
            return [
                item for item in self._routes.values()
                if deployment_id in {
                    item.active_deployment_id,
                    item.previous_deployment_id,
                    item.candidate_deployment_id,
                }
            ]

    def update_route(self, project_id: UUID, key: str, **fields: Any) -> DeploymentRoute | None:
        _check_route_fields(fields)
        with self._lock:
            current = self._routes.get((project_id, key))
            if current is None:
                return None
            updated = replace(current, **fields, updated_at=datetime.now(UTC))
            self._routes[(project_id, key)] = updated
            return updated

    def delete_route(self, project_id: UUID, key: str) -> bool:
        with self._lock:
            return self._routes.pop((project_id, key), None) is not None


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
            runtime_profile=row.get("runtime_profile"),
            readiness=row.get("readiness"),
        )

    @staticmethod
    def _route_row(row: dict[str, Any]) -> DeploymentRoute:
        return DeploymentRoute(
            id=row["id"],
            project_id=row["project_id"],
            key=str(row["key"]),
            name=str(row["name"]),
            active_deployment_id=row["active_deployment_id"],
            previous_deployment_id=row.get("previous_deployment_id"),
            candidate_deployment_id=row.get("candidate_deployment_id"),
            drain_timeout_seconds=int(row["drain_timeout_seconds"]),
            last_error=row.get("last_error"),
            updated_by=str(row["updated_by"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def add(self, deployment: Deployment) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO deployments (
                    id, project_id, algorithm_version_id, node_id, gpu_device_ids, status,
                    steps, created_by, created_at, updated_at, runtime_profile
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    deployment.id, deployment.project_id, deployment.algorithm_version_id,
                    deployment.node_id, Jsonb(list(deployment.gpu_device_ids)),
                    deployment.status.value, Jsonb(list(deployment.steps)),
                    deployment.created_by, deployment.created_at, deployment.updated_at,
                    Jsonb(deployment.runtime_profile) if deployment.runtime_profile else None,
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
            else Jsonb(list(value)) if name == "steps"
            else Jsonb(value) if name == "readiness" and value is not None else value
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

    # 服务路由 --------------------------------------------------------------

    def add_route(self, route: DeploymentRoute) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO deployment_routes (
                    id, project_id, key, name, active_deployment_id,
                    previous_deployment_id, candidate_deployment_id,
                    drain_timeout_seconds, last_error, updated_by, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    route.id, route.project_id, route.key, route.name,
                    route.active_deployment_id, route.previous_deployment_id,
                    route.candidate_deployment_id, route.drain_timeout_seconds,
                    route.last_error, route.updated_by, route.created_at, route.updated_at,
                ),
            )

    def get_route(self, project_id: UUID, key: str) -> DeploymentRoute | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM deployment_routes WHERE project_id = %s AND key = %s",
                (project_id, key),
            )
            row = cursor.fetchone()
            return self._route_row(row) if row else None

    def list_routes(self, project_id: UUID) -> list[DeploymentRoute]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM deployment_routes WHERE project_id = %s ORDER BY key",
                (project_id,),
            )
            return [self._route_row(row) for row in cursor.fetchall()]

    def list_pending_routes(self) -> list[DeploymentRoute]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM deployment_routes WHERE candidate_deployment_id IS NOT NULL "
                "ORDER BY updated_at"
            )
            return [self._route_row(row) for row in cursor.fetchall()]

    def list_routes_for_deployment(self, deployment_id: UUID) -> list[DeploymentRoute]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM deployment_routes
                WHERE active_deployment_id = %s
                   OR previous_deployment_id = %s
                   OR candidate_deployment_id = %s
                """,
                (deployment_id, deployment_id, deployment_id),
            )
            return [self._route_row(row) for row in cursor.fetchall()]

    def update_route(self, project_id: UUID, key: str, **fields: Any) -> DeploymentRoute | None:
        _check_route_fields(fields)
        assignments = ", ".join(f"{name} = %s" for name in fields)
        values = list(fields.values())
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE deployment_routes SET {assignments}, updated_at = now() "
                "WHERE project_id = %s AND key = %s RETURNING *",
                (*values, project_id, key),
            )
            row = cursor.fetchone()
            return self._route_row(row) if row else None

    def delete_route(self, project_id: UUID, key: str) -> bool:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM deployment_routes WHERE project_id = %s AND key = %s",
                (project_id, key),
            )
            return cursor.rowcount > 0


def _check_fields(fields: dict[str, Any]) -> None:
    unknown = set(fields) - UPDATABLE
    if unknown or not fields:
        raise ValueError(f"cannot update deployment fields: {sorted(unknown)}")


def _check_route_fields(fields: dict[str, Any]) -> None:
    unknown = set(fields) - ROUTE_UPDATABLE
    if unknown or not fields:
        raise ValueError(f"cannot update deployment route fields: {sorted(unknown)}")
