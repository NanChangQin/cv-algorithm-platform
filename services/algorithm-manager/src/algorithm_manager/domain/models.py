import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

MODEL_DIRECTORY = re.compile(r"[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*){0,2}")


class InstanceStatus(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    HEALTHY = "healthy"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    algorithm_version_id: str
    algorithm_key: str
    image: str
    container_name: str
    internal_port: int = 8000
    device: str = "cpu"
    memory_mb: int = 1024
    cpu_count: float = 1
    gpu_device_ids: tuple[str, ...] = ()
    # 挂载到 /models 的模型子目录；为空时使用 algorithm_key（训练发布的版本各有自己的目录）
    model_directory: str | None = None

    def __post_init__(self) -> None:
        if not self.algorithm_version_id.strip():
            raise ValueError("algorithm_version_id is required")
        if not self.algorithm_key or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
            for character in self.algorithm_key
        ):
            raise ValueError("algorithm_key must contain lowercase letters, digits, or hyphens")
        if not self.image.strip() or " " in self.image:
            raise ValueError("image must be a non-empty Docker image reference")
        if not self.container_name.startswith("cv-algorithm-"):
            raise ValueError("managed container names must start with cv-algorithm-")
        if not 1 <= self.internal_port <= 65535:
            raise ValueError("internal_port must be a valid TCP port")
        if self.device not in {"cpu", "gpu", "auto"}:
            raise ValueError("device must be cpu, gpu, or auto")
        if self.memory_mb < 256:
            raise ValueError("memory_mb must be at least 256")
        if not 0.1 <= self.cpu_count <= 64:
            raise ValueError("cpu_count must be between 0.1 and 64")
        if any(not item.isdigit() for item in self.gpu_device_ids):
            raise ValueError("gpu device ids must be numeric indexes")
        if self.device == "cpu" and self.gpu_device_ids:
            raise ValueError("cpu containers cannot declare gpu device ids")
        if self.model_directory is not None and not MODEL_DIRECTORY.fullmatch(
            self.model_directory
        ):
            raise ValueError("model_directory must be up to three lowercase path segments")


@dataclass(frozen=True, slots=True)
class AlgorithmInstance:
    id: str
    algorithm_version_id: str
    image: str
    container_name: str
    endpoint: str
    status: InstanceStatus
    device: str
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    last_used_at: datetime | None = None
    gpu_device_ids: tuple[str, ...] = ()

    @classmethod
    def created(cls, instance_id: str, spec: ContainerSpec, endpoint: str) -> "AlgorithmInstance":
        now = datetime.now(UTC)
        return cls(
            id=instance_id,
            algorithm_version_id=spec.algorithm_version_id,
            image=spec.image,
            container_name=spec.container_name,
            endpoint=endpoint,
            status=InstanceStatus.CREATED,
            device=spec.device,
            created_at=now,
            updated_at=now,
            last_used_at=now,
            gpu_device_ids=spec.gpu_device_ids,
        )

    def transition(self, status: InstanceStatus, error: str | None = None) -> "AlgorithmInstance":
        return replace(self, status=status, error=error, updated_at=datetime.now(UTC))

    def touch(self) -> "AlgorithmInstance":
        now = datetime.now(UTC)
        return replace(self, last_used_at=now, updated_at=now)
