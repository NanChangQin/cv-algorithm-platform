from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RuntimeInstance:
    id: str
    algorithm_version_id: str
    image: str
    container_name: str
    endpoint: str
    status: str
    device: str
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    last_used_at: datetime | None = None
    node_id: str = "local"
    gpu_device_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    algorithm_version_id: str
    algorithm_key: str
    image: str
    container_name: str
    internal_port: int
    device: str
    memory_mb: int
    cpu_count: float = 1
    gpu_device_ids: tuple[str, ...] = ()
    node_id: str | None = None
