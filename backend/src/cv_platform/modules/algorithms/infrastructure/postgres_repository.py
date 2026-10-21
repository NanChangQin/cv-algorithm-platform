from __future__ import annotations

from uuid import UUID

from cv_algorithm_sdk import AlgorithmManifest, AlgorithmStatus
from psycopg.errors import UniqueViolation
from psycopg.types.json import Json

from ....core.database import Database
from ..domain.entities import AlgorithmVersion, BuildJob, BuildStatus


class PostgresAlgorithmRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    @staticmethod
    def _from_row(row: dict[str, object]) -> AlgorithmVersion:
        return AlgorithmVersion(
            id=row["id"],
            manifest=AlgorithmManifest.model_validate(row["manifest"]),
            status=AlgorithmStatus(str(row["status"])),
            created_at=row["created_at"],
            image=str(row["image"]),
            internal_port=int(row["internal_port"]),
            package_path=row["package_storage_key"],
            package_sha256=row["package_sha256"],
            image_digest=row["image_digest"],
            created_by=str(row["created_by_label"]),
            traffic_weight=int(row["traffic_weight"]),
            project_id=row["project_id"],
        )

    def add(self, algorithm: AlgorithmVersion) -> None:
        manifest = algorithm.manifest.model_dump(mode="json")
        try:
            with self._database.connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO algorithm_versions (
                        id, project_id, algorithm_key, name, version, description,
                        schema_version, task_type, manifest, status, image, internal_port,
                        package_storage_key, package_sha256, image_digest, created_by,
                        created_by_label, traffic_weight, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, NULL, %s, %s, %s, %s
                    )
                    """,
                    (
                        algorithm.id,
                        algorithm.project_id,
                        algorithm.manifest.id,
                        algorithm.manifest.name,
                        algorithm.manifest.version,
                        algorithm.manifest.description,
                        algorithm.manifest.schema_version,
                        algorithm.manifest.task_type.value,
                        Json(manifest),
                        algorithm.status.value,
                        algorithm.image,
                        algorithm.internal_port,
                        algorithm.package_path,
                        algorithm.package_sha256,
                        algorithm.image_digest,
                        algorithm.created_by,
                        algorithm.traffic_weight,
                        algorithm.created_at,
                        algorithm.created_at,
                    ),
                )
        except UniqueViolation as exc:
            raise ValueError("algorithm version already exists in project") from exc

    def list(self) -> list[AlgorithmVersion]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM algorithm_versions WHERE deleted_at IS NULL ORDER BY created_at"
            )
            return [self._from_row(row) for row in cursor.fetchall()]

    def get(self, algorithm_id: UUID) -> AlgorithmVersion | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM algorithm_versions WHERE id = %s AND deleted_at IS NULL",
                (algorithm_id,),
            )
            row = cursor.fetchone()
            return self._from_row(row) if row else None

    def find_version(self, key: str, version: str) -> AlgorithmVersion | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM algorithm_versions
                WHERE algorithm_key = %s AND version = %s AND deleted_at IS NULL
                ORDER BY created_at LIMIT 1
                """,
                (key, version),
            )
            row = cursor.fetchone()
            return self._from_row(row) if row else None

    def save(self, algorithm: AlgorithmVersion) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE algorithm_versions SET
                    manifest = %s, status = %s, image = %s, internal_port = %s,
                    package_storage_key = %s, package_sha256 = %s,
                    image_digest = %s, traffic_weight = %s, updated_at = now()
                WHERE id = %s AND deleted_at IS NULL
                """,
                (
                    Json(algorithm.manifest.model_dump(mode="json")),
                    algorithm.status.value,
                    algorithm.image,
                    algorithm.internal_port,
                    algorithm.package_path,
                    algorithm.package_sha256,
                    algorithm.image_digest,
                    algorithm.traffic_weight,
                    algorithm.id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"algorithm does not exist: {algorithm.id}")

    def delete(self, algorithm_id: UUID) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE algorithm_versions SET deleted_at = now(), updated_at = now() "
                "WHERE id = %s",
                (algorithm_id,),
            )


class PostgresBuildJobRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def add(self, job: BuildJob) -> None:
        with self._database.connect() as connection:
            self._write(connection, job, insert=True)

    def save(self, job: BuildJob) -> None:
        with self._database.connect() as connection:
            self._write(connection, job, insert=False)

    @staticmethod
    def _write(connection, job: BuildJob, *, insert: bool) -> None:
        with connection.cursor() as cursor:
            if insert:
                cursor.execute(
                    """
                    INSERT INTO build_jobs (
                        id, algorithm_version_id, status, image_digest,
                        error_message, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        job.id,
                        job.algorithm_version_id,
                        job.status.value,
                        job.image_digest,
                        job.error_message,
                        job.created_at,
                        job.updated_at,
                    ),
                )
            else:
                cursor.execute(
                    """
                    UPDATE build_jobs SET status = %s, image_digest = %s,
                        error_message = %s, updated_at = %s WHERE id = %s
                    """,
                    (
                        job.status.value,
                        job.image_digest,
                        job.error_message,
                        job.updated_at,
                        job.id,
                    ),
                )
            for sequence, message in enumerate(job.logs):
                cursor.execute(
                    """
                    INSERT INTO build_job_logs (build_job_id, sequence, message)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (build_job_id, sequence)
                    DO UPDATE SET message = EXCLUDED.message
                    """,
                    (job.id, sequence, message),
                )

    def get(self, job_id: UUID) -> BuildJob | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM build_jobs WHERE id = %s", (job_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                "SELECT message FROM build_job_logs WHERE build_job_id = %s ORDER BY sequence",
                (job_id,),
            )
            logs = tuple(str(item["message"]) for item in cursor.fetchall())
            return BuildJob(
                id=row["id"],
                algorithm_version_id=row["algorithm_version_id"],
                status=BuildStatus(str(row["status"])),
                logs=logs,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                image_digest=row["image_digest"],
                error_message=row["error_message"],
            )
