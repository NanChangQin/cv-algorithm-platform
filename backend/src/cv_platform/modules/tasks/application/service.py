from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from cv_algorithm_sdk import AlgorithmStatus, TaskStatus

from ....core.errors import ApplicationError
from ....core.project_context import DEFAULT_PROJECT_ID
from ...algorithms.domain.repositories import AlgorithmRepository
from ...assets.domain.entities import ImageAsset
from ...assets.domain.repositories import AssetQuery, AssetRepository
from ..domain.entities import TERMINAL_STATUSES, InferenceTask
from ..domain.execution import TaskQueue
from ..domain.repositories import TaskQuery, TaskRepository, TaskSearch


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
        normalized_parameters, asset = self.validate_task(
            algorithm_version_id,
            parameters,
            asset_id=asset_id,
            asset_uri=asset_uri,
            node_id=node_id,
            gpu_device_ids=gpu_device_ids,
        )
        task = InferenceTask.queued(
            algorithm_version_id,
            asset.algorithm_uri,
            normalized_parameters,
            asset_id=asset.id,
            retry_of=retry_of,
            project_id=self._project_id,
            owner_id=self._actor,
            node_id=node_id,
            gpu_device_ids=gpu_device_ids,
        )
        self._tasks.add(task)
        self._queue.submit(str(task.id))
        return task

    def validate_task(
        self,
        algorithm_version_id: UUID,
        parameters: dict[str, Any],
        *,
        asset_id: UUID | None = None,
        asset_uri: str | None = None,
        node_id: str | None = None,
        gpu_device_ids: tuple[str, ...] = (),
    ) -> tuple[dict[str, Any], ImageAsset]:
        """校验算法、图片与参数但不创建任务，返回规范化参数和输入图片。"""
        algorithm = self._algorithms.get(algorithm_version_id)
        if algorithm is None or algorithm.project_id != self._project_id:
            raise ApplicationError("ALGORITHM_NOT_FOUND", "算法版本不存在", 404)
        if algorithm.status != AlgorithmStatus.AVAILABLE:
            raise ApplicationError("ALGORITHM_NOT_AVAILABLE", "算法版本当前不可用", 409)
        if gpu_device_ids and not node_id:
            raise ApplicationError("PLACEMENT_INVALID", "指定 GPU 时必须选择运行服务器")
        if gpu_device_ids and algorithm.manifest.runtime.device.value == "cpu":
            raise ApplicationError("PLACEMENT_INVALID", "该算法仅支持 CPU，不能指定 GPU")

        asset = self._resolve_asset(asset_id, asset_uri)
        try:
            normalized_parameters = algorithm.manifest.resolve_parameters(parameters)
        except ValueError as exc:
            raise ApplicationError(
                "PARAMETER_INVALID",
                f"算法参数不合法: {exc}",
            ) from exc
        return normalized_parameters, asset

    def list_tasks(
        self,
        *,
        algorithm_version_id: UUID | None = None,
        asset_id: UUID | None = None,
        statuses: tuple[TaskStatus, ...] = (),
        node_id: str | None = None,
        created_after: datetime | None = None,
        ids: tuple[UUID, ...] = (),
        search: TaskSearch | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[InferenceTask], int]:
        """返回当前页的任务和符合条件的总数。"""
        criteria = TaskQuery(
            project_id=self._project_id,
            algorithm_version_id=algorithm_version_id,
            asset_id=asset_id,
            statuses=statuses,
            node_id=node_id,
            created_after=created_after,
            ids=ids,
            search=search,
            limit=limit,
            offset=offset,
        )
        items = self._tasks.query(criteria)
        total = len(items) if limit is None and not offset else self._tasks.count(criteria)
        return items, total

    def resolve_search(self, text: str, nodes: Iterable[tuple[str, str]]) -> TaskSearch:
        """把搜索词解析成名称命中的算法、图片和节点；nodes 为 (节点 ID, 名称)。"""
        needle = text.lower()
        algorithm_ids = tuple(
            item.id
            for item in self._algorithms.list()
            if item.project_id == self._project_id
            and (needle in item.manifest.name.lower() or needle in item.manifest.id)
        )
        asset_ids = tuple(
            item.id for item in self._assets.query(AssetQuery(self._project_id, text=text))
        )
        node_ids = tuple(
            node_id for node_id, name in nodes
            if needle in node_id.lower() or needle in name.lower()
        )
        return TaskSearch(text, algorithm_ids, asset_ids, node_ids)

    def count_by_status(self) -> dict[TaskStatus, int]:
        return self._tasks.count_by_status(self._project_id)

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

    def _resolve_asset(self, asset_id: UUID | None, asset_uri: str | None) -> ImageAsset:
        if asset_id is not None:
            asset = self._assets.get(asset_id)
        elif asset_uri and asset_uri.startswith("file:///data/"):
            # asset_uri 只能指向本项目已上传的图片，否则任何人都能让算法读取 /data 下的其他文件
            asset = self._assets.find_by_algorithm_uri(self._project_id, asset_uri)
        else:
            raise ApplicationError("INPUT_INVALID", "必须选择已上传的图片资源")
        if asset is None or asset.project_id != self._project_id:
            raise ApplicationError("ASSET_NOT_FOUND", "图片资源不存在", 404)
        return asset
