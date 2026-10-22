from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from ...core.project_context import DEFAULT_PROJECT_ID


class WorkflowMode(StrEnum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    id: str
    algorithm_version_id: UUID
    parameters: dict[str, Any]
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Workflow:
    id: UUID
    name: str
    mode: WorkflowMode
    nodes: tuple[WorkflowNode, ...]
    created_at: datetime
    created_by: str = "local-admin"
    project_id: UUID = DEFAULT_PROJECT_ID

    @classmethod
    def create(
        cls,
        name: str,
        mode: WorkflowMode,
        nodes: list[WorkflowNode],
        project_id: UUID = DEFAULT_PROJECT_ID,
        created_by: str = "local-admin",
    ) -> "Workflow":
        return cls(
            uuid4(),
            name,
            mode,
            tuple(nodes),
            datetime.now(UTC),
            project_id=project_id,
            created_by=created_by,
        )


class WorkflowRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    id: UUID
    workflow_id: UUID
    asset_id: UUID
    status: WorkflowRunStatus
    node_tasks: dict[str, UUID]
    created_at: datetime
    updated_at: datetime
    error_message: str | None = None
    project_id: UUID = DEFAULT_PROJECT_ID

    @classmethod
    def queued(
        cls,
        workflow_id: UUID,
        asset_id: UUID,
        project_id: UUID = DEFAULT_PROJECT_ID,
    ) -> "WorkflowRun":
        now = datetime.now(UTC)
        return cls(
            uuid4(),
            workflow_id,
            asset_id,
            WorkflowRunStatus.QUEUED,
            {},
            now,
            now,
            project_id=project_id,
        )
