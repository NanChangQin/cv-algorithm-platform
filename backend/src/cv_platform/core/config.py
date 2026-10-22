from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CV_PLATFORM_", env_file=".env")

    app_name: str = "CV Algorithm Platform API"
    api_prefix: str = "/api/v1"
    # 为 true 时开放 /docs 接口文档，只用于本地开发
    debug: bool = False
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    algorithm_manager_url: str = "http://localhost:8010/api/v1"
    # 与各节点 Algorithm Manager 的 ALGORITHM_MANAGER_API_TOKEN 保持一致；为空时不发送令牌。
    manager_api_token: SecretStr | None = None
    storage_root: Path = Path("storage")
    package_root: Path = Path("storage/packages")
    algorithm_data_root: str = "/data"
    max_upload_bytes: int = 25 * 1024 * 1024
    max_image_pixels: int = 100_000_000
    max_video_upload_bytes: int = 2 * 1024 * 1024 * 1024
    prediction_timeout_seconds: float = 120
    task_worker_count: int = 2
    queue_backend: Literal["local", "postgres"] = "local"
    worker_poll_seconds: float = Field(default=0.5, ge=0.1, le=10)
    max_package_bytes: int = 2 * 1024 * 1024 * 1024
    max_package_extracted_bytes: int = 4 * 1024 * 1024 * 1024
    max_package_files: int = 10_000
    admin_username: str = "admin"
    admin_password: str = "ChangeMe-Local-123!"
    auth_enabled: bool = True
    session_ttl_seconds: int = 8 * 60 * 60
    secure_cookies: bool = False
    media_worker_url: str = "http://localhost:8020"
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60
    autoscaling_reconcile_seconds: int = Field(default=15, ge=5, le=3600)
    persistence_backend: Literal["memory", "postgres"] = "memory"
    database_url: str | None = None
    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "cv_platform"
    postgres_user: str = "cv_platform"
    postgres_password: str | None = None
    # 每个进程（Backend、Worker）各自的连接池上限
    database_pool_size: int = Field(default=10, ge=1, le=100)
    # 训练中心：训练镜像需预先构建到训练服务器（runtimes/yolo-trainer）
    training_image: str = "cv-platform/yolo-trainer:dev"
    max_dataset_bytes: int = 10 * 1024 * 1024 * 1024
    max_dataset_files: int = 200_000
    # 后台协调（训练准备、进度同步）的轮询间隔
    coordinator_interval_seconds: float = Field(default=3, ge=0.5, le=60)


@lru_cache
def get_settings() -> Settings:
    return Settings()
