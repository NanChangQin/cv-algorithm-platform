from __future__ import annotations

from datetime import datetime
from uuid import UUID

from cv_algorithm_sdk import AlgorithmResult, TaskStatus
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from ....core.database import Database
from ..domain.entities import InferenceTask
from ..domain.repositories import TaskQuery

RESULT_ADAPTER = TypeAdapter(AlgorithmResult)


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _where(criteria: TaskQuery) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if criteria.project_id is not None:
        clauses.append("project_id = %s")
        params.append(criteria.project_id)
    if criteria.algorithm_version_id is not None:
        clauses.append("algorithm_version_id = %s")
        params.append(criteria.algorithm_version_id)
    if criteria.deployment_id is not None:
        clauses.append("deployment_id = %s")
        params.append(criteria.deployment_id)
    if criteria.asset_id is not None:
        clauses.append("asset_id = %s")
        params.append(criteria.asset_id)
    if criteria.statuses:
        clauses.append("status = ANY(%s)")
        params.append([item.value for item in criteria.statuses])
    if criteria.node_id is not None:
        clauses.append("COALESCE(execution_node_id, node_id) = %s")
        params.append(criteria.node_id)
    if criteria.created_after is not None:
        clauses.append("created_at >= %s")
        params.append(criteria.created_after)
    if criteria.ids:
        clauses.append("id = ANY(%s)")
        params.append(list(criteria.ids))
    if criteria.search is not None:
        search = criteria.search
        pattern = _like_pattern(search.text)
        clauses.append(
            "(id::text ILIKE %s OR algorithm_version_id = ANY(%s::uuid[])"
            " OR asset_id = ANY(%s::uuid[])"
            " OR COALESCE(execution_node_id, node_id) = ANY(%s::text[])"
            " OR COALESCE(execution_node_id, node_id) ILIKE %s)"
        )
        params.extend(
            [
                pattern,
                list(search.algorithm_ids),
                list(search.asset_ids),
                list(search.node_ids),
                pattern,
            ]
        )
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


class PostgresTaskRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    @staticmethod
    def _from_row(row: dict[str, object]) -> InferenceTask:
        raw_result = row["result"]
        result = RESULT_ADAPTER.validate_python(raw_result) if raw_result is not None else None
        return InferenceTask(
            id=row["id"],
            algorithm_version_id=row["algorithm_version_id"],
            asset_id=row["asset_id"],
            asset_uri=str(row["asset_uri"]),
            parameters=dict(row["parameters"] or {}),
            status=TaskStatus(str(row["status"])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            cancelled_at=row["cancelled_at"],
            cancelled_by=row["cancelled_by_label"],
            container_id=row["container_id"],
            result=result,
            error_code=row["error_code"],
            error_message=row["error_message"],
            owner_id=str(row["owner_label"]),
            retry_of=row["retry_of"],
            project_id=row["project_id"],
            node_id=row["node_id"],
            deployment_id=row.get("deployment_id"),
            gpu_device_ids=tuple(row["gpu_device_ids"] or []),
            execution_node_id=row["execution_node_id"],
            execution_gpu_device_ids=tuple(row["execution_gpu_device_ids"] or []),
        )

    def add(self, task: InferenceTask) -> None:
        result = task.result.model_dump(mode="json") if task.result else None
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO inference_tasks (
                    id, project_id, owner_id, owner_label, algorithm_version_id,
                    asset_id, asset_uri, parameters, status, container_id,
                    result_type, result, error_code, error_message, retry_of,
                    cancelled_by, cancelled_by_label, created_at, updated_at,
                    started_at, completed_at, cancelled_at,
                    node_id, gpu_device_ids, execution_node_id, execution_gpu_device_ids,
                    deployment_id
                ) VALUES (
                    %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                """,
                (
                    task.id,
                    task.project_id,
                    task.owner_id,
                    task.algorithm_version_id,
                    task.asset_id,
                    task.asset_uri,
                    Jsonb(task.parameters),
                    task.status.value,
                    task.container_id,
                    task.result.type if task.result else None,
                    Jsonb(result) if result is not None else None,
                    task.error_code,
                    task.error_message,
                    task.retry_of,
                    task.cancelled_by,
                    task.created_at,
                    task.updated_at,
                    task.started_at,
                    task.completed_at,
                    task.cancelled_at,
                    task.node_id,
                    Jsonb(list(task.gpu_device_ids)),
                    task.execution_node_id,
                    Jsonb(list(task.execution_gpu_device_ids)),
                    task.deployment_id,
                ),
            )

    def save(self, task: InferenceTask) -> None:
        result = task.result.model_dump(mode="json") if task.result else None
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE inference_tasks SET
                    parameters = %s, status = %s, container_id = %s,
                    result_type = %s, result = %s, error_code = %s,
                    error_message = %s, cancelled_by_label = %s,
                    updated_at = %s, started_at = %s, completed_at = %s,
                    cancelled_at = %s, execution_node_id = %s, execution_gpu_device_ids = %s
                WHERE id = %s
                """,
                (
                    Jsonb(task.parameters),
                    task.status.value,
                    task.container_id,
                    task.result.type if task.result else None,
                    Jsonb(result) if result is not None else None,
                    task.error_code,
                    task.error_message,
                    task.cancelled_by,
                    task.updated_at,
                    task.started_at,
                    task.completed_at,
                    task.cancelled_at,
                    task.execution_node_id,
                    Jsonb(list(task.execution_gpu_device_ids)),
                    task.id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"task does not exist: {task.id}")

    def list(self) -> list[InferenceTask]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM inference_tasks ORDER BY created_at DESC")
            return [self._from_row(row) for row in cursor.fetchall()]

    def get(self, task_id: UUID) -> InferenceTask | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM inference_tasks WHERE id = %s", (task_id,))
            row = cursor.fetchone()
            return self._from_row(row) if row else None

    def query(self, criteria: TaskQuery) -> list[InferenceTask]:
        where, params = _where(criteria)
        sql = f"SELECT * FROM inference_tasks {where} ORDER BY created_at DESC, id DESC"
        if criteria.limit is not None:
            sql += " LIMIT %s"
            params.append(criteria.limit)
        if criteria.offset:
            sql += " OFFSET %s"
            params.append(criteria.offset)
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            return [self._from_row(row) for row in cursor.fetchall()]

    def count(self, criteria: TaskQuery) -> int:
        where, params = _where(criteria)
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) AS total FROM inference_tasks {where}", params)
            row = cursor.fetchone()
            return int(row["total"]) if row else 0

    def count_by_status(self, project_id: UUID | None = None) -> dict[TaskStatus, int]:
        where, params = _where(TaskQuery(project_id=project_id))
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT status, count(*) AS total FROM inference_tasks {where} GROUP BY status",
                params,
            )
            return {TaskStatus(str(row["status"])): int(row["total"]) for row in cursor.fetchall()}

    def count_by_algorithm(self, statuses: tuple[TaskStatus, ...]) -> dict[UUID, int]:
        where, params = _where(TaskQuery(statuses=statuses))
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT algorithm_version_id, count(*) AS total FROM inference_tasks "
                f"{where} GROUP BY algorithm_version_id",
                params,
            )
            return {row["algorithm_version_id"]: int(row["total"]) for row in cursor.fetchall()}

    def last_created_by_algorithm(self, project_id: UUID) -> dict[UUID, datetime]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT algorithm_version_id, max(created_at) AS latest FROM inference_tasks "
                "WHERE project_id = %s GROUP BY algorithm_version_id",
                (project_id,),
            )
            return {row["algorithm_version_id"]: row["latest"] for row in cursor.fetchall()}

    def inference_time_stats(self, project_id: UUID | None = None) -> tuple[float, int]:
        where, params = _where(TaskQuery(project_id=project_id))
        condition = f"{where} AND result IS NOT NULL" if where else "WHERE result IS NOT NULL"
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT avg((result->'timing'->>'inference_ms')::double precision) AS average, "
                f"count(*) AS samples FROM inference_tasks {condition}",
                params,
            )
            row = cursor.fetchone()
            if not row or not row["samples"]:
                return 0.0, 0
            return float(row["average"] or 0.0), int(row["samples"])

