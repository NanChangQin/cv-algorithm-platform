from datetime import datetime
from typing import Any
from uuid import UUID

from cv_algorithm_sdk import AlgorithmStatus, DeviceType, ResultType
from pydantic import BaseModel, ConfigDict

from ..domain.entities import AlgorithmVersion, BuildJob, BuildStatus


class AlgorithmResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    name: str
    version: str
    description: str
    task_type: ResultType
    device: DeviceType
    framework: str
    status: AlgorithmStatus
    image: str
    parameters: dict[str, Any]
    created_at: datetime
    package_sha256: str | None
    image_digest: str | None
    created_by: str
    traffic_weight: int
    container_status: str
    last_called_at: datetime | None
    project_id: UUID

    @classmethod
    def from_entity(
        cls,
        entity: AlgorithmVersion,
        *,
        container_status: str = "stopped",
        last_called_at: datetime | None = None,
    ) -> "AlgorithmResponse":
        manifest = entity.manifest
        return cls(
            id=entity.id,
            key=manifest.id,
            name=manifest.name,
            version=manifest.version,
            description=manifest.description,
            task_type=manifest.task_type,
            device=manifest.runtime.device,
            framework=manifest.runtime.framework,
            status=entity.status,
            image=entity.image,
            parameters={key: value.model_dump() for key, value in manifest.parameters.items()},
            created_at=entity.created_at,
            package_sha256=entity.package_sha256,
            image_digest=entity.image_digest,
            created_by=entity.created_by,
            traffic_weight=entity.traffic_weight,
            container_status=container_status,
            last_called_at=last_called_at,
            project_id=entity.project_id,
        )


class BuildJobResponse(BaseModel):
    id: UUID
    algorithm_version_id: UUID
    status: BuildStatus
    logs: list[str]
    created_at: datetime
    updated_at: datetime
    image_digest: str | None
    error_message: str | None

    @classmethod
    def from_entity(cls, entity: BuildJob) -> "BuildJobResponse":
        return cls(
            id=entity.id,
            algorithm_version_id=entity.algorithm_version_id,
            status=entity.status,
            logs=list(entity.logs),
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            image_digest=entity.image_digest,
            error_message=entity.error_message,
        )
