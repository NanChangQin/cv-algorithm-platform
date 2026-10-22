from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from cv_algorithm_sdk import AlgorithmManifest, AlgorithmStatus

from ....core.project_context import DEFAULT_PROJECT_ID


@dataclass(frozen=True, slots=True)
class AlgorithmVersion:
    id: UUID
    manifest: AlgorithmManifest
    status: AlgorithmStatus
    created_at: datetime
    image: str
    internal_port: int
    package_path: str | None = None
    package_sha256: str | None = None
    image_digest: str | None = None
    created_by: str = "local-admin"
    traffic_weight: int = 100
    project_id: UUID = DEFAULT_PROJECT_ID
    # 节点模型目录下挂载给容器的子目录；为空时使用算法 ID（预置算法的约定）
    model_directory: str | None = None
    # 由训练结果发布的版本：来源训练、数据集、参数、指标和权重校验值
    lineage: dict[str, Any] | None = None

    @classmethod
    def available(
        cls,
        manifest: AlgorithmManifest,
        image: str,
        internal_port: int = 8000,
        algorithm_id: UUID | None = None,
        project_id: UUID = DEFAULT_PROJECT_ID,
        created_by: str = "local-admin",
    ) -> "AlgorithmVersion":
        return cls(
            id=algorithm_id or uuid4(),
            manifest=manifest,
            status=AlgorithmStatus.AVAILABLE,
            created_at=datetime.now(UTC),
            image=image,
            internal_port=internal_port,
            project_id=project_id,
            created_by=created_by,
        )

    @classmethod
    def uploaded(
        cls,
        manifest: AlgorithmManifest,
        image: str,
        package_path: str,
        package_sha256: str,
        project_id: UUID = DEFAULT_PROJECT_ID,
        created_by: str = "local-admin",
    ) -> "AlgorithmVersion":
        return cls(
            id=uuid4(),
            manifest=manifest,
            status=AlgorithmStatus.UPLOADED,
            created_at=datetime.now(UTC),
            image=image,
            internal_port=8000,
            package_path=package_path,
            package_sha256=package_sha256,
            project_id=project_id,
            created_by=created_by,
        )

    def with_status(
        self,
        status: AlgorithmStatus,
        *,
        image_digest: str | None = None,
    ) -> "AlgorithmVersion":
        return replace(
            self,
            status=status,
            image_digest=image_digest or self.image_digest,
        )

    def with_traffic_weight(self, weight: int) -> "AlgorithmVersion":
        if not 0 <= weight <= 100:
            raise ValueError("traffic weight must be between 0 and 100")
        return replace(self, traffic_weight=weight)


class BuildStatus(StrEnum):
    QUEUED = "queued"
    BUILDING = "building"
    TESTING = "testing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BuildJob:
    id: UUID
    algorithm_version_id: UUID
    status: BuildStatus
    logs: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    image_digest: str | None = None
    error_message: str | None = None

    @classmethod
    def queued(cls, algorithm_version_id: UUID) -> "BuildJob":
        now = datetime.now(UTC)
        return cls(uuid4(), algorithm_version_id, BuildStatus.QUEUED, (), now, now)

    def update(
        self,
        status: BuildStatus,
        message: str,
        *,
        image_digest: str | None = None,
        error_message: str | None = None,
    ) -> "BuildJob":
        timestamp = datetime.now(UTC)
        log_line = f"{timestamp.isoformat()} {message}"
        return replace(
            self,
            status=status,
            logs=(*self.logs, log_line),
            updated_at=timestamp,
            image_digest=image_digest or self.image_digest,
            error_message=error_message,
        )
