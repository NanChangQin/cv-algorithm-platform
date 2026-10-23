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
    DRAINING = "draining"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


ACTIVE_STATUSES = frozenset({
    DeploymentStatus.QUEUED, DeploymentStatus.PREPARING, DeploymentStatus.RUNNING,
    DeploymentStatus.DRAINING, DeploymentStatus.STOPPING,
})

# 每次部署依次执行的检查步骤
STEPS: tuple[tuple[str, str], ...] = (
    ("node", "检查服务器"),
    ("image", "准备镜像"),
    ("weights", "准备权重"),
    ("start", "启动实例"),
    ("verify", "验证模型"),
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
    runtime_profile: dict[str, Any] | None = None
    readiness: dict[str, Any] | None = None

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


@dataclass(frozen=True, slots=True)
class DeploymentRoute:
    id: UUID
    project_id: UUID
    key: str
    name: str
    active_deployment_id: UUID
    previous_deployment_id: UUID | None
    candidate_deployment_id: UUID | None
    drain_timeout_seconds: int
    last_error: str | None
    updated_by: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        project_id: UUID,
        key: str,
        name: str,
        active_deployment_id: UUID,
        actor: str,
        *,
        drain_timeout_seconds: int = 120,
    ) -> DeploymentRoute:
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            project_id=project_id,
            key=key,
            name=name,
            active_deployment_id=active_deployment_id,
            previous_deployment_id=None,
            candidate_deployment_id=None,
            drain_timeout_seconds=drain_timeout_seconds,
            last_error=None,
            updated_by=actor,
            created_at=now,
            updated_at=now,
        )
