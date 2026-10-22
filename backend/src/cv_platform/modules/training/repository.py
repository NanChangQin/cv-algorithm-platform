from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ...core.database import Database
from .domain import ACTIVE_STATUSES, TrainingDataset, TrainingRun, TrainingStatus

# update() 允许修改的字段；状态变化都走带预期状态的条件更新，避免和并发的停止请求互相覆盖
UPDATABLE = {
    "status", "stage", "progress", "metrics", "artifacts", "error", "logs", "started_at",
    "finished_at", "gpu_device_ids",
}
JSON_FIELDS = {"progress", "metrics", "artifacts", "logs", "gpu_device_ids"}


class InMemoryTrainingRepository:
    def __init__(self) -> None:
        self._datasets: dict[UUID, TrainingDataset] = {}
        self._runs: dict[UUID, TrainingRun] = {}
        self._lock = RLock()

    def add_dataset(self, dataset: TrainingDataset) -> None:
        with self._lock:
            self._datasets[dataset.id] = dataset

    def get_dataset(self, dataset_id: UUID) -> TrainingDataset | None:
        with self._lock:
            return self._datasets.get(dataset_id)

    def list_datasets(self, project_id: UUID) -> list[TrainingDataset]:
        with self._lock:
            items = [item for item in self._datasets.values() if item.project_id == project_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def delete_dataset(self, dataset_id: UUID) -> None:
        with self._lock:
            self._datasets.pop(dataset_id, None)

    def add_run(self, run: TrainingRun) -> None:
        with self._lock:
            self._runs[run.id] = run

    def get_run(self, run_id: UUID) -> TrainingRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(self, project_id: UUID) -> list[TrainingRun]:
        with self._lock:
            items = [item for item in self._runs.values() if item.project_id == project_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def list_active(self) -> list[TrainingRun]:
        with self._lock:
            return [item for item in self._runs.values() if item.status in ACTIVE_STATUSES]

    def count_runs_for_dataset(self, dataset_id: UUID) -> int:
        with self._lock:
            return sum(item.dataset_id == dataset_id for item in self._runs.values())

    def update(
        self,
        run_id: UUID,
        expected: Iterable[TrainingStatus] | None = None,
        **fields: Any,
    ) -> bool:
        _check_fields(fields)
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or (expected is not None and run.status not in set(expected)):
                return False
            self._runs[run_id] = replace(run, **fields, updated_at=datetime.now(UTC))
            return True

    def delete_run(self, run_id: UUID) -> None:
        with self._lock:
            self._runs.pop(run_id, None)


class PostgresTrainingRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    @staticmethod
    def _dataset(row: dict[str, Any]) -> TrainingDataset:
        return TrainingDataset(
            id=row["id"],
            project_id=row["project_id"],
            name=str(row["name"]),
            class_names=tuple(row["class_names"]),
            stats=dict(row["stats"]),
            size_bytes=int(row["size_bytes"]),
            sha256=str(row["sha256"]),
            created_by=str(row["created_by"]),
            created_at=row["created_at"],
            format=str(row["format"]),
        )

    @staticmethod
    def _run(row: dict[str, Any]) -> TrainingRun:
        return TrainingRun(
            id=row["id"],
            project_id=row["project_id"],
            name=str(row["name"]),
            dataset_id=row["dataset_id"],
            model=str(row["model"]),
            parameters=dict(row["parameters"]),
            node_id=str(row["node_id"]),
            gpu_device_ids=tuple(row["gpu_device_ids"]),
            status=TrainingStatus(row["status"]),
            created_by=str(row["created_by"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            stage=row["stage"],
            progress=row["progress"],
            metrics=tuple(row["metrics"]),
            artifacts=dict(row["artifacts"]),
            error=row["error"],
            logs=tuple(row["logs"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def add_dataset(self, dataset: TrainingDataset) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO training_datasets (
                    id, project_id, name, format, class_names, stats, size_bytes, sha256,
                    created_by, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    dataset.id, dataset.project_id, dataset.name, dataset.format,
                    Jsonb(list(dataset.class_names)), Jsonb(dataset.stats), dataset.size_bytes,
                    dataset.sha256, dataset.created_by, dataset.created_at,
                ),
            )

    def get_dataset(self, dataset_id: UUID) -> TrainingDataset | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM training_datasets WHERE id = %s", (dataset_id,))
            row = cursor.fetchone()
            return self._dataset(row) if row else None

    def list_datasets(self, project_id: UUID) -> list[TrainingDataset]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM training_datasets WHERE project_id = %s ORDER BY created_at DESC",
                (project_id,),
            )
            return [self._dataset(row) for row in cursor.fetchall()]

    def delete_dataset(self, dataset_id: UUID) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM training_datasets WHERE id = %s", (dataset_id,))

    def add_run(self, run: TrainingRun) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO training_runs (
                    id, project_id, name, dataset_id, model, parameters, node_id,
                    gpu_device_ids, status, created_by, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run.id, run.project_id, run.name, run.dataset_id, run.model,
                    Jsonb(run.parameters), run.node_id, Jsonb(list(run.gpu_device_ids)),
                    run.status.value, run.created_by, run.created_at, run.updated_at,
                ),
            )

    def get_run(self, run_id: UUID) -> TrainingRun | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM training_runs WHERE id = %s", (run_id,))
            row = cursor.fetchone()
            return self._run(row) if row else None

    def list_runs(self, project_id: UUID) -> list[TrainingRun]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM training_runs WHERE project_id = %s ORDER BY created_at DESC",
                (project_id,),
            )
            return [self._run(row) for row in cursor.fetchall()]

    def list_active(self) -> list[TrainingRun]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM training_runs WHERE status = ANY(%s) ORDER BY created_at",
                ([status.value for status in ACTIVE_STATUSES],),
            )
            return [self._run(row) for row in cursor.fetchall()]

    def count_runs_for_dataset(self, dataset_id: UUID) -> int:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) AS total FROM training_runs WHERE dataset_id = %s", (dataset_id,)
            )
            return int(cursor.fetchone()["total"])

    def update(
        self,
        run_id: UUID,
        expected: Iterable[TrainingStatus] | None = None,
        **fields: Any,
    ) -> bool:
        _check_fields(fields)
        assignments = ", ".join(f"{name} = %s" for name in fields)
        values = [_column_value(name, value) for name, value in fields.items()]
        condition = ""
        if expected is not None:
            condition = " AND status = ANY(%s)"
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE training_runs SET {assignments}, updated_at = now() "
                f"WHERE id = %s{condition}",
                (
                    *values,
                    run_id,
                    *([[status.value for status in expected]] if expected is not None else []),
                ),
            )
            return cursor.rowcount > 0

    def delete_run(self, run_id: UUID) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM training_runs WHERE id = %s", (run_id,))


def _check_fields(fields: dict[str, Any]) -> None:
    unknown = set(fields) - UPDATABLE
    if unknown or not fields:
        raise ValueError(f"cannot update training run fields: {sorted(unknown)}")


def _column_value(name: str, value: Any) -> Any:
    if isinstance(value, TrainingStatus):
        return value.value
    if name in JSON_FIELDS:
        return Jsonb(list(value) if isinstance(value, tuple) else value)
    return value
