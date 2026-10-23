from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ModelFile:
    """需要同步到节点模型目录的文件（主服务器上的路径和校验值）。"""

    name: str
    path: Path
    sha256: str
    size: int


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
    runtime_scope: str = ""
    prediction_timeout_seconds: int | None = None


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
    runtime_scope: str = ""
    prediction_timeout_seconds: int | None = None
    node_id: str | None = None
    # 挂载给容器的模型子目录，为空时节点使用 algorithm_key
    model_directory: str | None = None
    # 启动前要确保节点上已有的模型文件（训练发布的版本）
    model_files: tuple[ModelFile, ...] = ()
    shm_mb: int = 64
    runtime_config: dict | None = None
