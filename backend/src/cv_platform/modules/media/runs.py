from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from ...core.errors import ApplicationError
from ..tasks.application.service import TaskService
from ..assets.application.service import AssetService
from .service import InMemoryMediaSourceService
from .worker_gateway import MediaWorkerGateway
from ...core.project_context import DEFAULT_PROJECT_ID
from ...core.database import Database
from .postgres_service import PostgresMediaSourceService


class MediaRunStatus(StrEnum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MediaInferenceRun:
    id: UUID
    source_id: UUID
    algorithm_version_id: UUID
    status: MediaRunStatus
    frame_task_ids: tuple[UUID, ...]
    created_at: datetime
    updated_at: datetime
    error_message: str | None = None
    project_id: UUID = DEFAULT_PROJECT_ID

    @classmethod
    def queued(
        cls,
        source_id: UUID,
        algorithm_version_id: UUID,
        project_id: UUID = DEFAULT_PROJECT_ID,
    ) -> "MediaInferenceRun":
        now = datetime.now(UTC)
        return cls(uuid4(), source_id, algorithm_version_id, MediaRunStatus.QUEUED, (), now, now, project_id=project_id)


class MediaRunService:
    def __init__(
        self,
        sources: InMemoryMediaSourceService | PostgresMediaSourceService,
        worker: MediaWorkerGateway,
        database: Database | None = None,
    ) -> None:
        self._sources = sources
        self._worker = worker
        self._database = database
        self._items: dict[UUID, MediaInferenceRun] = {}
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cv-media-run")

    def create(
        self,
        source_id: UUID,
        algorithm_version_id: UUID,
        interval_seconds: float,
        max_frames: int,
        parameters: dict[str, object],
        tasks: TaskService,
        assets: AssetService,
        project_id: UUID = DEFAULT_PROJECT_ID,
    ) -> MediaInferenceRun:
        self._sources.get(source_id, project_id)
        run = MediaInferenceRun.queued(source_id, algorithm_version_id, project_id)
        if self._database is None:
            with self._lock:
                self._items[run.id] = run
        else:
            with self._database.connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO media_inference_runs (
                        id, project_id, source_id, algorithm_version_id, status,
                        parameters, interval_seconds, max_frames, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run.id, run.project_id, run.source_id, run.algorithm_version_id,
                        run.status.value, Jsonb(parameters), interval_seconds, max_frames,
                        run.created_at, run.updated_at,
                    ),
                )
        self._executor.submit(
            self._execute,
            run.id,
            interval_seconds,
            max_frames,
            parameters,
            tasks,
            assets,
        )
        return run

    def get(self, run_id: UUID, project_id: UUID | None = None) -> MediaInferenceRun:
        if self._database is None:
            with self._lock:
                run = self._items.get(run_id)
        else:
            with self._database.connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT r.*, COALESCE(array_agg(t.task_id ORDER BY t.frame_index)
                        FILTER (WHERE t.task_id IS NOT NULL), '{}') AS frame_task_ids
                    FROM media_inference_runs r
                    LEFT JOIN media_run_tasks t ON t.media_run_id = r.id
                    WHERE r.id = %s GROUP BY r.id
                    """,
                    (run_id,),
                )
                row = cursor.fetchone()
                run = self._from_row(row) if row else None
        if run is None or (project_id is not None and run.project_id != project_id):
            raise ApplicationError("MEDIA_RUN_NOT_FOUND", "媒体推理运行不存在", 404)
        return run

    def list(self, project_id: UUID | None = None) -> list[MediaInferenceRun]:
        if self._database is None:
            with self._lock:
                return sorted(
                    [item for item in self._items.values() if project_id is None or item.project_id == project_id],
                    key=lambda item: item.created_at,
                    reverse=True,
                )
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.*, COALESCE(array_agg(t.task_id ORDER BY t.frame_index)
                    FILTER (WHERE t.task_id IS NOT NULL), '{}') AS frame_task_ids
                FROM media_inference_runs r
                LEFT JOIN media_run_tasks t ON t.media_run_id = r.id
                WHERE (%s::uuid IS NULL OR r.project_id = %s)
                GROUP BY r.id ORDER BY r.created_at DESC
                """,
                (project_id, project_id),
            )
            return [self._from_row(row) for row in cursor.fetchall()]

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def recover_incomplete(self) -> None:
        for run in self.list():
            if run.status in {
                MediaRunStatus.QUEUED,
                MediaRunStatus.EXTRACTING,
                MediaRunStatus.RUNNING,
            }:
                self._save(
                    replace(
                        run,
                        status=MediaRunStatus.FAILED,
                        error_message="平台重启导致媒体任务中断",
                    )
                )

    def _execute(
        self,
        run_id: UUID,
        interval_seconds: float,
        max_frames: int,
        parameters: dict[str, object],
        tasks: TaskService,
        assets: AssetService,
    ) -> None:
        run = self.get(run_id)
        try:
            run = self._save(replace(run, status=MediaRunStatus.EXTRACTING))
            source = self._sources.get(run.source_id)
            frames = self._worker.extract(
                source.uri,
                interval_seconds,
                max_frames,
                run.id,
            )
            if not frames:
                raise ApplicationError("MEDIA_EMPTY", "媒体源没有可提取的画面", 422)
            task_ids = []
            for frame in frames:
                asset = assets.register_local_image(
                    str(frame["relative_path"]),
                    f"frame-{int(frame['index']):08d}.jpg",
                    remove_source=True,
                )
                task = tasks.create_task(
                    run.algorithm_version_id,
                    parameters,
                    asset_id=asset.id,
                )
                task_ids.append(task.id)
            self._save(
                replace(
                    run,
                    status=MediaRunStatus.RUNNING,
                    frame_task_ids=tuple(task_ids),
                )
            )
        except Exception as exc:
            self._save(
                replace(
                    run,
                    status=MediaRunStatus.FAILED,
                    error_message=str(exc),
                )
            )

    def refresh(self, run_id: UUID, tasks: TaskService) -> MediaInferenceRun:
        run = self.get(run_id)
        if run.status is not MediaRunStatus.RUNNING:
            return run
        frame_tasks = [tasks.get_task(task_id) for task_id in run.frame_task_ids]
        if any(task.status.value == "failed" for task in frame_tasks):
            return self._save(replace(run, status=MediaRunStatus.FAILED, error_message="帧任务失败"))
        if frame_tasks and all(task.status.value == "completed" for task in frame_tasks):
            return self._save(replace(run, status=MediaRunStatus.COMPLETED))
        return run

    def _save(self, run: MediaInferenceRun) -> MediaInferenceRun:
        run = replace(run, updated_at=datetime.now(UTC))
        if self._database is None:
            with self._lock:
                self._items[run.id] = run
        else:
            with self._database.connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE media_inference_runs SET status = %s, error_message = %s,
                        updated_at = %s WHERE id = %s
                    """,
                    (run.status.value, run.error_message, run.updated_at, run.id),
                )
                cursor.execute("DELETE FROM media_run_tasks WHERE media_run_id = %s", (run.id,))
                for frame_index, task_id in enumerate(run.frame_task_ids):
                    cursor.execute(
                        """
                        INSERT INTO media_run_tasks (media_run_id, task_id, frame_index)
                        VALUES (%s, %s, %s)
                        """,
                        (run.id, task_id, frame_index),
                    )
        return run

    @staticmethod
    def _from_row(row: dict[str, object]) -> MediaInferenceRun:
        return MediaInferenceRun(
            id=row["id"],
            source_id=row["source_id"],
            algorithm_version_id=row["algorithm_version_id"],
            status=MediaRunStatus(str(row["status"])),
            frame_task_ids=tuple(row["frame_task_ids"] or ()),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            error_message=row["error_message"],
            project_id=row["project_id"],
        )
