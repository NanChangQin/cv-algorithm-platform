from datetime import datetime
from typing import Any
from uuid import UUID

from cv_algorithm_sdk import AlgorithmResult, TaskStatus
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateTaskRequest(BaseModel):
    algorithm_version_id: UUID | None = None
    algorithm_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    asset_id: UUID | None = None
    asset_uri: str | None = Field(default=None, min_length=1, max_length=2_048)
    parameters: dict[str, Any] = Field(default_factory=dict)
    node_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    gpu_device_ids: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def require_input(self) -> "CreateTaskRequest":
        if self.asset_id is None and self.asset_uri is None:
            raise ValueError("asset_id is required")
        if self.algorithm_version_id is None and self.algorithm_key is None:
            raise ValueError("algorithm_version_id or algorithm_key is required")
        if self.gpu_device_ids and not self.node_id:
            raise ValueError("指定 GPU 时必须选择运行服务器")
        if any(not item.isascii() or not item.isdigit() for item in self.gpu_device_ids):
            raise ValueError("GPU 编号必须为数字索引")
        if len(set(self.gpu_device_ids)) != len(self.gpu_device_ids):
            raise ValueError("GPU 编号不能重复")
        return self


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    algorithm_version_id: UUID
    asset_id: UUID | None
    parameters: dict[str, Any]
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    cancelled_by: str | None
    container_id: str | None
    error_code: str | None
    error_message: str | None
    retry_of: UUID | None
    project_id: UUID
    node_id: str | None
    gpu_device_ids: list[str]
    execution_node_id: str | None
    execution_gpu_device_ids: list[str]


class TaskResultResponse(BaseModel):
    task_id: UUID
    result: AlgorithmResult


class ResultArchiveRequest(BaseModel):
    task_ids: list[UUID] = Field(min_length=1, max_length=100)


class TaskSummaryResponse(BaseModel):
    total: int
    by_status: dict[str, int]
