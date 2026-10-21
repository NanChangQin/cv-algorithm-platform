from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALGORITHM_MANAGER_", env_file=".env")

    app_name: str = "CV Algorithm Manager"
    docker_network: str = "cv-algorithm-platform_default"
    host_data_root: Path = Path("/srv/cv-platform/data")
    host_model_root: Path = Path("/srv/cv-platform/models")
    package_root: Path = Path("/packages")
    build_network: str = "bridge"
    health_timeout_seconds: float = 60
    idle_timeout_seconds: int = 1800
    health_monitor_seconds: int = Field(default=15, ge=5, le=300)
    health_failure_threshold: int = Field(default=3, ge=1, le=20)
    data_root: Path = Path("/data")
    max_input_bytes: int = 25 * 1024 * 1024
    prediction_timeout_seconds: float = 120
    gpu_access_mode: Literal["device-request", "runtime"] = "device-request"
    # 与主平台共享的访问令牌。设置后，除 /health 外的接口都要求 Authorization: Bearer <令牌>。
    # 节点端口暴露在局域网时务必设置，否则局域网内任何主机都能启停算法容器。
    api_token: SecretStr | None = None
    # 只允许运行这些前缀的算法镜像（平台构建与预置的算法镜像都以 cv-platform/ 开头）。
    allowed_image_prefixes: list[str] = Field(default_factory=lambda: ["cv-platform/"])


@lru_cache
def get_settings() -> Settings:
    return Settings()
