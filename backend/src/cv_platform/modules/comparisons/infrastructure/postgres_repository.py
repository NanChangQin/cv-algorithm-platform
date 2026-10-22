from __future__ import annotations

from uuid import UUID

from ....core.database import Database
from ..domain.entities import AlgorithmComparison


class PostgresComparisonRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    @staticmethod
    def _from_row(row: dict[str, object]) -> AlgorithmComparison:
        return AlgorithmComparison(
            id=row["id"],
            asset_id=row["asset_id"],
            task_ids=tuple(row["task_ids"] or ()),
            created_at=row["created_at"],
            owner_id=str(row["owner_label"]),
            project_id=row["project_id"],
        )

    def add(self, comparison: AlgorithmComparison) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO algorithm_comparisons (
                    id, project_id, owner_id, owner_label, asset_id, created_at
                ) VALUES (%s, %s, NULL, %s, %s, %s)
                """,
                (
                    comparison.id,
                    comparison.project_id,
                    comparison.owner_id,
                    comparison.asset_id,
                    comparison.created_at,
                ),
            )
            for position, task_id in enumerate(comparison.task_ids):
                cursor.execute(
                    """
                    INSERT INTO comparison_tasks (comparison_id, task_id, position)
                    VALUES (%s, %s, %s)
                    """,
                    (comparison.id, task_id, position),
                )

    def get(self, comparison_id: UUID) -> AlgorithmComparison | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.*, COALESCE(array_agg(ct.task_id ORDER BY ct.position)
                    FILTER (WHERE ct.task_id IS NOT NULL), '{}') AS task_ids
                FROM algorithm_comparisons c
                LEFT JOIN comparison_tasks ct ON ct.comparison_id = c.id
                WHERE c.id = %s GROUP BY c.id
                """,
                (comparison_id,),
            )
            row = cursor.fetchone()
            return self._from_row(row) if row else None

    def list(self) -> list[AlgorithmComparison]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.*, COALESCE(array_agg(ct.task_id ORDER BY ct.position)
                    FILTER (WHERE ct.task_id IS NOT NULL), '{}') AS task_ids
                FROM algorithm_comparisons c
                LEFT JOIN comparison_tasks ct ON ct.comparison_id = c.id
                GROUP BY c.id ORDER BY c.created_at DESC
                """
            )
            return [self._from_row(row) for row in cursor.fetchall()]

    def list_recent(self, project_id: UUID, limit: int) -> list[AlgorithmComparison]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.*, COALESCE(array_agg(ct.task_id ORDER BY ct.position)
                    FILTER (WHERE ct.task_id IS NOT NULL), '{}') AS task_ids
                FROM algorithm_comparisons c
                LEFT JOIN comparison_tasks ct ON ct.comparison_id = c.id
                WHERE c.project_id = %s
                GROUP BY c.id ORDER BY c.created_at DESC LIMIT %s
                """,
                (project_id, limit),
            )
            return [self._from_row(row) for row in cursor.fetchall()]
