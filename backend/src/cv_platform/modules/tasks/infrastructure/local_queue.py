import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event, RLock
from uuid import UUID

from cv_algorithm_sdk import TaskStatus

from ....core.errors import ApplicationError
from ...algorithms.domain.repositories import AlgorithmRepository
from ...assets.domain.repositories import AssetRepository
from ...instances.application.service import RuntimeInstanceService
from ..domain.execution import AlgorithmPredictionGateway
from ..domain.repositories import TaskRepository

logger = logging.getLogger(__name__)


class LocalTaskQueue:
    """In-process development queue. Replace with Redis/Celery at server persistence phase."""

    def __init__(
        self,
        tasks: TaskRepository,
        algorithms: AlgorithmRepository,
        assets: AssetRepository,
        instances: RuntimeInstanceService,
        predictions: AlgorithmPredictionGateway,
        worker_count: int,
    ) -> None:
        self._tasks = tasks
        self._algorithms = algorithms
        self._assets = assets
        self._instances = instances
        self._predictions = predictions
        self._executor = ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="cv-inference",
        )
        self._cancel_events: dict[UUID, Event] = {}
        self._lock = RLock()

    def submit(self, task_id: str) -> None:
        parsed_id = UUID(task_id)
        with self._lock:
            self._cancel_events[parsed_id] = Event()
        self._executor.submit(self._execute, parsed_id)

    def cancel(self, task_id: str) -> None:
        parsed_id = UUID(task_id)
        with self._lock:
            event = self._cancel_events.setdefault(parsed_id, Event())
            event.set()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def execute_now(self, task_id: UUID) -> None:
        """Execute a task claimed by the durable worker."""
        self._execute(task_id)

    def _execute(self, task_id: UUID) -> None:
        try:
            task = self._required_task(task_id)
            if self._cancelled(task_id):
                return
            task = task.transition(TaskStatus.PREPARING)
            self._tasks.save(task)

            algorithm = self._algorithms.get(task.algorithm_version_id)
            if algorithm is None or algorithm.project_id != task.project_id:
                raise ApplicationError("ALGORITHM_NOT_FOUND", "算法版本不存在", 404)
            if task.asset_id is not None:
                asset = self._assets.get(task.asset_id)
                if asset is None or asset.project_id != task.project_id:
                    raise ApplicationError("ASSET_NOT_FOUND", "图片资源不存在", 404)
            if self._cancelled(task_id):
                return

            task = task.transition(TaskStatus.STARTING)
            self._tasks.save(task)
            instance = self._instances.start_algorithm(
                task.algorithm_version_id,
                task.project_id,
                node_id=task.node_id,
                gpu_device_ids=task.gpu_device_ids,
            )
            if self._cancelled(task_id):
                return

            task = task.transition(
                TaskStatus.RUNNING,
                container_id=instance.id,
                execution_node_id=instance.node_id,
                execution_gpu_device_ids=instance.gpu_device_ids,
            )
            self._tasks.save(task)
            result = self._predictions.predict(
                instance,
                str(task.id),
                task.asset_uri,
                task.parameters,
            )
            try:
                self._instances.touch(instance.id, task.project_id)
            except ApplicationError:
                logger.warning(
                    "could not refresh instance last-used timestamp",
                    extra={"task_id": str(task.id), "container_id": instance.id},
                )
            if self._cancelled(task_id):
                return
            self._tasks.save(task.transition(TaskStatus.COMPLETED, result=result))
        except ApplicationError as exc:
            self._fail(task_id, exc.code, exc.message)
        except Exception:
            logger.exception("inference task failed", extra={"task_id": str(task_id)})
            self._fail(task_id, "TASK_EXECUTION_FAILED", "推理任务执行失败")
        finally:
            with self._lock:
                self._cancel_events.pop(task_id, None)

    def _cancelled(self, task_id: UUID) -> bool:
        task = self._required_task(task_id)
        if task.status is TaskStatus.CANCELLED:
            return True
        with self._lock:
            event = self._cancel_events.get(task_id)
        if event is None or not event.is_set():
            return False
        if task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            self._tasks.save(
                task.transition(TaskStatus.CANCELLED, cancelled_by="system")
            )
        return True

    def _fail(self, task_id: UUID, code: str, message: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or task.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            return
        self._tasks.save(
            task.transition(TaskStatus.FAILED, error_code=code, error_message=message)
        )

    def _required_task(self, task_id: UUID):
        task = self._tasks.get(task_id)
        if task is None:
            raise ApplicationError("TASK_NOT_FOUND", "任务不存在", 404)
        return task
