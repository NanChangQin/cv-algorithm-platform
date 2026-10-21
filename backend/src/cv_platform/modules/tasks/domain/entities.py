from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from cv_algorithm_sdk import AlgorithmResult, TaskStatus

from ....core.project_context import DEFAULT_PROJECT_ID

TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


@dataclass(frozen=True, slots=True)
class InferenceTask:
    id: UUID
    algorithm_version_id: UUID
    asset_id: UUID | None
    asset_uri: str
    parameters: dict[str, Any]
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancelled_by: str | None = None
    container_id: str | None = None
    result: AlgorithmResult | None = None
    error_code: str | None = None
    error_message: str | None = None
    owner_id: str = "local-admin"
    retry_of: UUID | None = None
    project_id: UUID = DEFAULT_PROJECT_ID
    node_id: str | None = None
    gpu_device_ids: tuple[str, ...] = ()
    execution_node_id: str | None = None
    execution_gpu_device_ids: tuple[str, ...] = ()

    @classmethod
    def queued(
        cls,
        algorithm_version_id: UUID,
        asset_uri: str,
        parameters: dict[str, Any],
        *,
        asset_id: UUID | None = None,
        retry_of: UUID | None = None,
        project_id: UUID = DEFAULT_PROJECT_ID,
        owner_id: str = "local-admin",
        node_id: str | None = None,
        gpu_device_ids: tuple[str, ...] = (),
    ) -> "InferenceTask":
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            algorithm_version_id=algorithm_version_id,
            asset_id=asset_id,
            asset_uri=asset_uri,
            parameters=dict(parameters),
            status=TaskStatus.QUEUED,
            created_at=now,
            updated_at=now,
            retry_of=retry_of,
            project_id=project_id,
            owner_id=owner_id,
            node_id=node_id,
            gpu_device_ids=gpu_device_ids,
        )

    def transition(
        self,
        status: TaskStatus,
        *,
        container_id: str | None = None,
        result: AlgorithmResult | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        cancelled_by: str | None = None,
        execution_node_id: str | None = None,
        execution_gpu_device_ids: tuple[str, ...] | None = None,
    ) -> "InferenceTask":
        if self.status in TERMINAL_STATUSES:
            raise ValueError(f"cannot transition terminal task from {self.status} to {status}")
        now = datetime.now(UTC)
        return replace(
            self,
            status=status,
            updated_at=now,
            started_at=self.started_at or (now if status is TaskStatus.RUNNING else None),
            completed_at=now if status in {TaskStatus.COMPLETED, TaskStatus.FAILED} else None,
            cancelled_at=now if status is TaskStatus.CANCELLED else None,
            cancelled_by=cancelled_by,
            container_id=container_id or self.container_id,
            result=result,
            error_code=error_code,
            error_message=error_message,
            execution_node_id=execution_node_id or self.execution_node_id,
            execution_gpu_device_ids=(
                self.execution_gpu_device_ids
                if execution_gpu_device_ids is None else execution_gpu_device_ids
            ),
        )
