from __future__ import annotations

from dataclasses import replace
from threading import RLock
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from ...core.database import Database
from .domain import DatasetItem, EvaluationDataset, EvaluationRun


class InMemoryEvaluationRepository:
    def __init__(self) -> None:
        self._datasets: dict[UUID, EvaluationDataset] = {}
        self._runs: dict[UUID, EvaluationRun] = {}
        self._lock = RLock()

    def add_dataset(self, dataset: EvaluationDataset) -> None:
        with self._lock:
            self._datasets[dataset.id] = dataset

    def list_datasets(self, project_id: UUID) -> list[EvaluationDataset]:
        with self._lock:
            items = [item for item in self._datasets.values() if item.project_id == project_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def get_dataset(self, dataset_id: UUID) -> EvaluationDataset | None:
        with self._lock:
            return self._datasets.get(dataset_id)

    def delete_dataset(self, dataset_id: UUID) -> None:
        with self._lock:
            self._datasets.pop(dataset_id, None)
            for run_id in [run.id for run in self._runs.values() if run.dataset_id == dataset_id]:
                del self._runs[run_id]

    def add_run(self, run: EvaluationRun) -> None:
        with self._lock:
            self._runs[run.id] = run

    def list_runs(self, project_id: UUID) -> list[EvaluationRun]:
        with self._lock:
            items = [item for item in self._runs.values() if item.project_id == project_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def get_run(self, run_id: UUID) -> EvaluationRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def save_report(self, run_id: UUID, report: dict[str, Any]) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                self._runs[run_id] = replace(run, report=report)


class PostgresEvaluationRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    @staticmethod
    def _dataset(row: dict[str, Any]) -> EvaluationDataset:
        return EvaluationDataset(
            id=row["id"],
            project_id=row["project_id"],
            name=str(row["name"]),
            task_type=str(row["task_type"]),
            items=tuple(
                DatasetItem(UUID(item["asset_id"]), dict(item["ground_truth"]))
                for item in row["items"]
            ),
            created_by=str(row["created_by"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _run(row: dict[str, Any]) -> EvaluationRun:
        return EvaluationRun(
            id=row["id"],
            project_id=row["project_id"],
            dataset_id=row["dataset_id"],
            algorithm_version_ids=tuple(UUID(item) for item in row["algorithm_version_ids"]),
            task_ids=dict(row["task_ids"]),
            created_by=str(row["created_by"]),
            created_at=row["created_at"],
            report=row["report"],
        )

    def add_dataset(self, dataset: EvaluationDataset) -> None:
        items = [
            {"asset_id": str(item.asset_id), "ground_truth": item.ground_truth}
            for item in dataset.items
        ]
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO evaluation_datasets
                    (id, project_id, name, task_type, items, created_by, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    dataset.id, dataset.project_id, dataset.name, dataset.task_type,
                    Jsonb(items), dataset.created_by, dataset.created_at,
                ),
            )

    def list_datasets(self, project_id: UUID) -> list[EvaluationDataset]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM evaluation_datasets WHERE project_id = %s ORDER BY created_at DESC",
                (project_id,),
            )
            return [self._dataset(row) for row in cursor.fetchall()]

    def get_dataset(self, dataset_id: UUID) -> EvaluationDataset | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM evaluation_datasets WHERE id = %s", (dataset_id,))
            row = cursor.fetchone()
            return self._dataset(row) if row else None

    def delete_dataset(self, dataset_id: UUID) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM evaluation_datasets WHERE id = %s", (dataset_id,))

    def add_run(self, run: EvaluationRun) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO evaluation_runs (
                    id, project_id, dataset_id, algorithm_version_ids, task_ids, report,
                    created_by, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run.id, run.project_id, run.dataset_id,
                    Jsonb([str(item) for item in run.algorithm_version_ids]),
                    Jsonb(run.task_ids), Jsonb(run.report) if run.report else None,
                    run.created_by, run.created_at,
                ),
            )

    def list_runs(self, project_id: UUID) -> list[EvaluationRun]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM evaluation_runs WHERE project_id = %s ORDER BY created_at DESC",
                (project_id,),
            )
            return [self._run(row) for row in cursor.fetchall()]

    def get_run(self, run_id: UUID) -> EvaluationRun | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM evaluation_runs WHERE id = %s", (run_id,))
            row = cursor.fetchone()
            return self._run(row) if row else None

    def save_report(self, run_id: UUID, report: dict[str, Any]) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE evaluation_runs SET report = %s WHERE id = %s", (Jsonb(report), run_id)
            )
