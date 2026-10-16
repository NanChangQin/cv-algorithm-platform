from datetime import datetime
from typing import Any
from uuid import UUID

from cv_algorithm_sdk import AlgorithmStatus, TaskStatus

from ....core.errors import ApplicationError
from ...algorithms.domain.repositories import AlgorithmRepository
from ...assets.domain.repositories import AssetRepository
from ..domain.entities import InferenceTask, TERMINAL_STATUSES
from ..domain.execution import TaskQueue
from ..domain.repositories import TaskRepository
from ....core.project_context import DEFAULT_PROJECT_ID


class TaskService:
    def __init__(
        self,
        tasks: TaskRepository,
        algorithms: AlgorithmRepository,
        assets: AssetRepository,
        queue: TaskQueue,
        project_id: UUID = DEFAULT_PROJECT_ID,
        actor: str = "local-admin",
    ) -> None:
        self._tasks = tasks
        self._algorithms = algorithms
        self._assets = assets
        self._queue = queue
        self._project_id = project_id
        self._actor = actor

    def create_task(
        self,
        algorithm_version_id: UUID,
        parameters: dict[str, Any],
        *,
        asset_id: UUID | None = None,
        asset_uri: str | None = None,
        retry_of: UUID | None = None,
        node_id: str | None = None,
        gpu_device_ids: tuple[str, ...] = (),
    ) -> InferenceTask:
        algorithm = self._algorithms.get(algorithm_version_id)
        if algorithm is None or algorithm.project_id != self._project_id:
            raise ApplicationError("ALGORITHM_NOT_FOUND", "算法版本不存在", 404)
        if algorithm.status != AlgorithmStatus.AVAILABLE:
            raise ApplicationError("ALGORITHM_NOT_AVAILABLE", "算法版本当前不可用", 409)
        if gpu_device_ids and not node_id:
            raise ApplicationError("PLACEMENT_INVALID", "指定 GPU 时必须选择运行服务器")
        if gpu_device_ids and algorithm.manifest.runtime.device.value == "cpu":
            raise ApplicationError("PLACEMENT_INVALID", "该算法仅支持 CPU，不能指定 GPU")

        resolved_uri = self._resolve_asset_uri(asset_id, asset_uri)
        try:
            normalized_parameters = algorithm.manifest.resolve_parameters(parameters)
        except ValueError as exc:
            raise ApplicationError(
                "PARAMETER_INVALID",
                f"算法参数不合法: {exc}",
            ) from exc

        task = InferenceTask.queued(
            algorithm_version_id,
            resolved_uri,
            normalized_parameters,
            asset_id=asset_id,
            retry_of=retry_of,
            project_id=self._project_id,
            owner_id=self._actor,
            node_id=node_id,
            gpu_device_ids=gpu_device_ids,
        )
        self._tasks.add(task)
        self._queue.submit(str(task.id))
        return task

    def list_tasks(
        self,
        *,
        algorithm_version_id: UUID | None = None,
        asset_id: UUID | None = None,
        status: TaskStatus | None = None,
        created_after: datetime | None = None,
    ) -> list[InferenceTask]:
        tasks = [item for item in self._tasks.list() if item.project_id == self._project_id]
        return [
            task
            for task in tasks
            if (algorithm_version_id is None or task.algorithm_version_id == algorithm_version_id)
            and (asset_id is None or task.asset_id == asset_id)
            and (status is None or task.status is status)
            and (created_after is None or task.created_at >= created_after)
        ]

    def get_task(self, task_id: UUID) -> InferenceTask:
        task = self._tasks.get(task_id)
        if task is None or task.project_id != self._project_id:
            raise ApplicationError("TASK_NOT_FOUND", "任务不存在", 404)
        return task

    def cancel(self, task_id: UUID, actor: str | None = None) -> InferenceTask:
        task = self.get_task(task_id)
        if task.status in TERMINAL_STATUSES:
            raise ApplicationError("TASK_NOT_CANCELLABLE", "当前任务状态不可取消", 409)
        cancelled = task.transition(
            TaskStatus.CANCELLED,
            cancelled_by=actor or self._actor,
        )
        self._tasks.save(cancelled)
        self._queue.cancel(str(task.id))
        return cancelled

    def retry(self, task_id: UUID) -> InferenceTask:
        original = self.get_task(task_id)
        if original.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise ApplicationError("TASK_NOT_RETRYABLE", "仅失败或已取消任务可重试", 409)
        return self.create_task(
            original.algorithm_version_id,
            original.parameters,
            asset_id=original.asset_id,
            asset_uri=original.asset_uri,
            retry_of=original.id,
            node_id=original.node_id,
            gpu_device_ids=original.gpu_device_ids,
        )

    def _resolve_asset_uri(self, asset_id: UUID | None, asset_uri: str | None) -> str:
        if asset_id is not None:
            asset = self._assets.get(asset_id)
            if asset is None or asset.project_id != self._project_id:
                raise ApplicationError("ASSET_NOT_FOUND", "图片资源不存在", 404)
            return asset.algorithm_uri
        if asset_uri and asset_uri.startswith("file:///data/"):
            return asset_uri
        raise ApplicationError("INPUT_INVALID", "必须选择已上传的图片资源")
