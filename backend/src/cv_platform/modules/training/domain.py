from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class TrainingModel:
    key: str
    label: str
    description: str


# 训练镜像里预置了这些预训练权重（runtimes/yolo-trainer/Dockerfile）
TRAINING_MODELS: tuple[TrainingModel, ...] = (
    TrainingModel("yolo26n.pt", "YOLO26n", "最快，适合边缘设备和快速验证"),
    TrainingModel("yolo26s.pt", "YOLO26s", "速度和精度均衡"),
    TrainingModel("yolo26m.pt", "YOLO26m", "精度更高，显存和时间约为 s 的两倍"),
    TrainingModel("yolo11n.pt", "YOLO11n", "上一代轻量模型"),
    TrainingModel("yolo11s.pt", "YOLO11s", "上一代均衡模型"),
    TrainingModel("yolo11m.pt", "YOLO11m", "上一代高精度模型"),
)


@dataclass(frozen=True, slots=True)
class TrainingDataset:
    id: UUID
    project_id: UUID
    name: str
    class_names: tuple[str, ...]
    # 各划分图片数、各类别框数、校验提示等
    stats: dict[str, Any]
    size_bytes: int
    sha256: str
    created_by: str
    created_at: datetime
    format: str = "yolo-detect"
    family_id: UUID | None = None
    version: int = 1
    parent_id: UUID | None = None
    source_sha256: str | None = None
    split_config: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": str(self.id), "name": self.name,
            "family_id": str(self.family_id or self.id), "version": self.version,
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "sha256": self.sha256, "source_sha256": self.source_sha256,
            "split_config": self.split_config, "splits": self.stats.get("splits", {}),
        }

    @classmethod
    def create(
        cls,
        project_id: UUID,
        name: str,
        class_names: tuple[str, ...],
        stats: dict[str, Any],
        size_bytes: int,
        sha256: str,
        actor: str,
        dataset_id: UUID | None = None,
    ) -> TrainingDataset:
        return cls(
            dataset_id or uuid4(), project_id, name, class_names, stats, size_bytes, sha256,
            actor, datetime.now(UTC),
        )


class TrainingStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


ACTIVE_STATUSES = frozenset({
    TrainingStatus.QUEUED, TrainingStatus.PREPARING, TrainingStatus.RUNNING,
    TrainingStatus.STOPPING,
})
FINISHED_STATUSES = frozenset({
    TrainingStatus.COMPLETED, TrainingStatus.STOPPED, TrainingStatus.FAILED,
})


@dataclass(frozen=True, slots=True)
class TrainingRun:
    id: UUID
    project_id: UUID
    name: str
    dataset_id: UUID
    model: str
    parameters: dict[str, Any]
    node_id: str
    gpu_device_ids: tuple[str, ...]
    status: TrainingStatus
    created_by: str
    created_at: datetime
    updated_at: datetime
    # 准备阶段当前步骤，例如“上传数据集 40%”
    stage: str | None = None
    progress: dict[str, Any] | None = None
    # results.csv 的每一行（每轮一行）
    metrics: tuple[dict[str, float], ...] = ()
    # 已收回到主服务器的训练产物：相对路径 → 字节数
    artifacts: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    logs: tuple[str, ...] = ()
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @classmethod
    def queued(
        cls,
        project_id: UUID,
        name: str,
        dataset_id: UUID,
        model: str,
        parameters: dict[str, Any],
        node_id: str,
        gpu_device_ids: tuple[str, ...],
        actor: str,
    ) -> TrainingRun:
        now = datetime.now(UTC)
        return cls(
            uuid4(), project_id, name, dataset_id, model, parameters, node_id, gpu_device_ids,
            TrainingStatus.QUEUED, actor, now, now,
        )
