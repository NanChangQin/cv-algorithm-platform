from __future__ import annotations

from uuid import UUID

from cv_algorithm_sdk import AlgorithmResult, TaskStatus
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from ....core.database import Database
from ..domain.entities import InferenceTask


RESULT_ADAPTER = TypeAdapter(AlgorithmResult)


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
                    node_id, gpu_device_ids, execution_node_id, execution_gpu_device_ids
                ) VALUES (
                    %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
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
