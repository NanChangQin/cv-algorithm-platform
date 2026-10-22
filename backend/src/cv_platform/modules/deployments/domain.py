from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class DeploymentStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


ACTIVE_STATUSES = frozenset({
    DeploymentStatus.QUEUED, DeploymentStatus.PREPARING, DeploymentStatus.RUNNING,
    DeploymentStatus.STOPPING,
})

# 每次部署依次执行的检查步骤
STEPS: tuple[tuple[str, str], ...] = (
    ("node", "检查服务器"),
    ("image", "准备镜像"),
    ("weights", "准备权重"),
    ("start", "启动实例"),
)


def initial_steps() -> tuple[dict[str, Any], ...]:
    return tuple(
        {"key": key, "label": label, "status": "pending", "detail": None} for key, label in STEPS
    )


@dataclass(frozen=True, slots=True)
class Deployment:
    id: UUID
    project_id: UUID
    algorithm_version_id: UUID
    node_id: str
    gpu_device_ids: tuple[str, ...]
    status: DeploymentStatus
    created_by: str
    created_at: datetime
    updated_at: datetime
    # 每步：{key, label, status: pending/running/done/skipped/failed, detail}
    steps: tuple[dict[str, Any], ...] = ()
    instance_id: str | None = None
    error: str | None = None
    started_at: datetime | None = None

    @classmethod
    def queued(
        cls,
        project_id: UUID,
        algorithm_version_id: UUID,
        node_id: str,
        gpu_device_ids: tuple[str, ...],
        actor: str,
    ) -> Deployment:
        now = datetime.now(UTC)
        return cls(
            uuid4(), project_id, algorithm_version_id, node_id, gpu_device_ids,
            DeploymentStatus.QUEUED, actor, now, now, initial_steps(),
        )
