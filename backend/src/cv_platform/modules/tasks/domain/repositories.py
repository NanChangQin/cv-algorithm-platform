from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from cv_algorithm_sdk import TaskStatus

from .entities import InferenceTask


@dataclass(frozen=True, slots=True)
class TaskSearch:
    """搜索词命中任务 ID，或命中名称已解析成 ID 的算法、图片、运行节点。"""

    text: str
    algorithm_ids: tuple[UUID, ...] = ()
    asset_ids: tuple[UUID, ...] = ()
    node_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskQuery:
    # None 表示所有项目，只用于平台内部的统计和恢复
    project_id: UUID | None = None
    algorithm_version_id: UUID | None = None
    asset_id: UUID | None = None
    statuses: tuple[TaskStatus, ...] = ()
    # 按实际运行节点匹配，尚未调度的任务按指定节点匹配
    node_id: str | None = None
    created_after: datetime | None = None
    ids: tuple[UUID, ...] = ()
    search: TaskSearch | None = None
    limit: int | None = None
    offset: int = 0


class TaskRepository(Protocol):
    def add(self, task: InferenceTask) -> None: ...

    def save(self, task: InferenceTask) -> None: ...

    def list(self) -> list[InferenceTask]: ...

    def get(self, task_id: UUID) -> InferenceTask | None: ...

    def query(self, criteria: TaskQuery) -> list[InferenceTask]:
        """按创建时间倒序返回符合条件的任务。"""
        ...

    def count(self, criteria: TaskQuery) -> int:
        """符合条件的任务总数，忽略 limit 和 offset。"""
        ...

    def count_by_status(self, project_id: UUID | None = None) -> dict[TaskStatus, int]: ...

    def count_by_algorithm(self, statuses: tuple[TaskStatus, ...]) -> dict[UUID, int]: ...

    def last_created_by_algorithm(self, project_id: UUID) -> dict[UUID, datetime]: ...

    def inference_time_stats(self, project_id: UUID | None = None) -> tuple[float, int]:
        """已出结果任务的平均推理耗时（毫秒）和样本数。"""
        ...
