from uuid import UUID

from ....core.database import Database


class PostgresTaskQueue:
    """Durable dispatcher backed by the already-persisted inference task row."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def submit(self, task_id: str) -> None:
        parsed_id = UUID(task_id)
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE inference_tasks SET updated_at = now() WHERE id = %s AND status = 'queued'",
                (parsed_id,),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"queued task does not exist: {parsed_id}")

    def cancel(self, task_id: str) -> None:
        # TaskService persists the cancelled state before notifying the queue.
        UUID(task_id)

    def shutdown(self) -> None:
        return None


class PostgresWorkClaimer:
    """Atomically claims durable work with PostgreSQL SKIP LOCKED."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def recover_interrupted(self) -> tuple[int, int]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE inference_tasks
                SET status = 'queued', container_id = NULL, updated_at = now(),
                    error_code = NULL, error_message = NULL,
                    started_at = NULL, completed_at = NULL
                WHERE status IN ('preparing', 'starting', 'running')
                """
            )
            task_count = cursor.rowcount
            cursor.execute(
                """
                WITH interrupted AS (
                    UPDATE build_jobs
                    SET status = 'queued', error_message = NULL, updated_at = now()
                    WHERE status IN ('building', 'testing')
                    RETURNING algorithm_version_id
                )
                UPDATE algorithm_versions
                SET status = 'uploaded', updated_at = now()
                WHERE id IN (SELECT algorithm_version_id FROM interrupted)
                """
            )
            build_count = cursor.rowcount
        return task_count, build_count

    def claim_task(self) -> UUID | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                WITH next_task AS (
                    SELECT id FROM inference_tasks
                    WHERE status = 'queued'
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE inference_tasks AS task
                SET status = 'preparing', updated_at = now()
                FROM next_task
                WHERE task.id = next_task.id
                RETURNING task.id
                """
            )
            row = cursor.fetchone()
            return row["id"] if row else None

    def claim_build(self) -> UUID | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                WITH next_build AS (
                    SELECT id FROM build_jobs
                    WHERE status = 'queued'
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE build_jobs AS job
                SET status = 'building', updated_at = now()
                FROM next_build
                WHERE job.id = next_build.id
                RETURNING job.id
                """
            )
            row = cursor.fetchone()
            return row["id"] if row else None

    def queue_depths(self) -> dict[str, int]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    count(*) FILTER (WHERE status = 'queued') AS inference_queued,
                    count(*) FILTER (WHERE status IN ('preparing', 'starting', 'running'))
                        AS inference_active
                FROM inference_tasks
                """
            )
            task_row = cursor.fetchone()
            cursor.execute(
                """
                SELECT
                    count(*) FILTER (WHERE status = 'queued') AS builds_queued,
                    count(*) FILTER (WHERE status IN ('building', 'testing')) AS builds_active
                FROM build_jobs
                """
            )
            build_row = cursor.fetchone()
        return {
            "inference_queued": int(task_row["inference_queued"]),
            "inference_active": int(task_row["inference_active"]),
            "builds_queued": int(build_row["builds_queued"]),
            "builds_active": int(build_row["builds_active"]),
        }
