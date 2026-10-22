from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class DatasetItem:
    asset_id: UUID
    ground_truth: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    id: UUID
    project_id: UUID
    name: str
    task_type: str
    items: tuple[DatasetItem, ...]
    created_by: str
    created_at: datetime

    @classmethod
    def create(
        cls, project_id: UUID, name: str, task_type: str, items: tuple[DatasetItem, ...], actor: str
    ) -> EvaluationDataset:
        return cls(uuid4(), project_id, name, task_type, items, actor, datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    id: UUID
    project_id: UUID
    dataset_id: UUID
    algorithm_version_ids: tuple[UUID, ...]
    # "算法版本 ID:图片 ID" → 推理任务 ID
    task_ids: dict[str, str]
    created_by: str
    created_at: datetime
    # 全部任务结束后缓存的报告，之后不再重新计算
    report: dict[str, Any] | None = field(default=None)

    @classmethod
    def create(
        cls,
        project_id: UUID,
        dataset_id: UUID,
        algorithm_version_ids: tuple[UUID, ...],
        task_ids: dict[str, str],
        actor: str,
    ) -> EvaluationRun:
        return cls(
            uuid4(), project_id, dataset_id, algorithm_version_ids, task_ids, actor,
            datetime.now(UTC),
        )
