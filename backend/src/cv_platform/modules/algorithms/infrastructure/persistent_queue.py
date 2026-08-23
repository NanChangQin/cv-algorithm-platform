from uuid import UUID

from ....core.database import Database


class PostgresBuildQueue:
    """Durable dispatcher backed by the already-persisted build job row."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def submit(self, job_id: UUID) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE build_jobs SET updated_at = now() WHERE id = %s AND status = 'queued'",
                (job_id,),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"queued build does not exist: {job_id}")

    def shutdown(self) -> None:
        return None
