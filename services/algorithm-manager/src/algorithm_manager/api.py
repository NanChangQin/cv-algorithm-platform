import json
import re
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx
from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .application.errors import ManagerError
from .application.service import InstanceService
from .config import get_settings
from .domain.models import AlgorithmInstance, ContainerSpec, InstanceStatus

IMAGE_REFERENCE = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?$"
)
# 平台自身的服务镜像同样以 cv-platform/ 开头，但不能当作算法容器启动。
PLATFORM_SERVICE_IMAGES = {
    "cv-platform/algorithm-manager",
    "cv-platform/backend",
    "cv-platform/database-migrator",
    "cv-platform/frontend",
    "cv-platform/media-worker",
}


def validate_algorithm_image(image: str) -> str:
    if not IMAGE_REFERENCE.fullmatch(image):
        raise ValueError("镜像名称不合法")
    prefixes = get_settings().allowed_image_prefixes
    if not any(image.startswith(prefix) for prefix in prefixes):
        raise ValueError(f"只允许运行以 {'、'.join(prefixes)} 开头的算法镜像")
    if image.rsplit(":", 1)[0] in PLATFORM_SERVICE_IMAGES:
        raise ValueError("不能把平台服务镜像作为算法容器运行")
    return image


class EnsureInstanceRequest(BaseModel):
    algorithm_version_id: str = Field(min_length=1)
    algorithm_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    image: str = Field(min_length=1)
    container_name: str = Field(pattern=r"^cv-algorithm-[a-z0-9-]+$")
    internal_port: int = Field(default=8000, ge=1, le=65535)
    device: str = Field(default="cpu", pattern=r"^(cpu|gpu|auto)$")
    memory_mb: int = Field(default=1024, ge=256, le=262_144)
    cpu_count: float = Field(default=1, ge=0.1, le=64)
    gpu_device_ids: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("image")
    @classmethod
    def check_image(cls, value: str) -> str:
        return validate_algorithm_image(value)

    def to_spec(self) -> ContainerSpec:
        data = self.model_dump()
        data["gpu_device_ids"] = tuple(data["gpu_device_ids"])
        return ContainerSpec(**data)


class InstanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    algorithm_version_id: str
    image: str
    container_name: str
    endpoint: str
    status: InstanceStatus
    device: str
    created_at: datetime
    updated_at: datetime
    error: str | None
    last_used_at: datetime | None
    gpu_device_ids: list[str]

    @classmethod
    def from_entity(cls, instance: AlgorithmInstance) -> "InstanceResponse":
        return cls.model_validate(instance)


class EnsureReplicasRequest(EnsureInstanceRequest):
    replicas: int = Field(ge=0, le=100)


def build_router(get_service: object) -> APIRouter:
    router = APIRouter(prefix="/instances", tags=["instances"])

    @router.get("", response_model=list[InstanceResponse])
    def list_instances(service: InstanceService = Depends(get_service)) -> list[InstanceResponse]:
        return [InstanceResponse.from_entity(item) for item in service.list_instances()]

    @router.post("/ensure", response_model=InstanceResponse)
    def ensure_instance(
        payload: EnsureInstanceRequest,
        service: InstanceService = Depends(get_service),
    ) -> InstanceResponse:
        return InstanceResponse.from_entity(service.ensure_running(payload.to_spec()))

    @router.post("/replicas/ensure", response_model=list[InstanceResponse])
    def ensure_replicas(
        payload: EnsureReplicasRequest,
        service: InstanceService = Depends(get_service),
    ) -> list[InstanceResponse]:
        data = payload.model_dump(exclude={"replicas"})
        data["gpu_device_ids"] = tuple(data["gpu_device_ids"])
        instances = service.ensure_replicas(ContainerSpec(**data), payload.replicas)
        return [InstanceResponse.from_entity(item) for item in instances]

    @router.post("/{instance_id}/stop", response_model=InstanceResponse)
    def stop_instance(
        instance_id: str,
        service: InstanceService = Depends(get_service),
    ) -> InstanceResponse:
        return InstanceResponse.from_entity(service.stop(instance_id))

    @router.delete("/{instance_id}", status_code=204)
    def remove_instance(
        instance_id: str,
        service: InstanceService = Depends(get_service),
    ) -> Response:
        service.remove(instance_id)
        return Response(status_code=204)

    @router.get("/{instance_id}/logs", response_model=list[str])
    def instance_logs(
        instance_id: str,
        tail: int = 200,
        service: InstanceService = Depends(get_service),
    ) -> list[str]:
        return service.logs(instance_id, tail)

    @router.post("/{instance_id}/touch", response_model=InstanceResponse)
    def touch_instance(
        instance_id: str,
        service: InstanceService = Depends(get_service),
    ) -> InstanceResponse:
        return InstanceResponse.from_entity(service.touch(instance_id))

    @router.post("/{instance_id}/predict")
    def predict_remote(
        instance_id: str,
        request_id: str = Form(min_length=1, max_length=128),
        parameters: str = Form(default="{}"),
        file: UploadFile = File(),
        service: InstanceService = Depends(get_service),
    ) -> Response:
        instance = service.get_instance(instance_id)
        if instance.status is not InstanceStatus.HEALTHY:
            raise ManagerError("INSTANCE_NOT_READY", "远端算法实例尚未就绪", 409)
        try:
            parsed_parameters = json.loads(parameters)
        except ValueError as exc:
            raise ManagerError("INPUT_INVALID", "算法参数必须为 JSON 对象") from exc
        if not isinstance(parsed_parameters, dict):
            raise ManagerError("INPUT_INVALID", "算法参数必须为 JSON 对象")
        settings = get_settings()
        input_root = settings.data_root / "remote-inputs"
        input_root.mkdir(parents=True, exist_ok=True)
        # Algorithms run as uid 10001 and mount /data read-only.
        input_root.chmod(0o755)
        input_path: Path | None = None
        try:
            with NamedTemporaryFile(dir=input_root, suffix=".input", delete=False) as target:
                input_path = Path(target.name)
                size = 0
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_input_bytes:
                        raise ManagerError("INPUT_TOO_LARGE", "远端输入图片超过大小限制", 413)
                    target.write(chunk)
            if size == 0:
                raise ManagerError("INPUT_INVALID", "远端输入图片为空")
            input_path.chmod(0o644)
            service.touch(instance.id)
            response = httpx.post(
                f"{instance.endpoint}/predict",
                json={
                    "request_id": request_id,
                    "input": {"asset_uri": f"file:///data/remote-inputs/{input_path.name}"},
                    "parameters": parsed_parameters,
                },
                timeout=settings.prediction_timeout_seconds,
            )
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type", "application/json"),
            )
        except httpx.TimeoutException as exc:
            raise ManagerError("PREDICTION_TIMEOUT", "远端算法推理超时", 504) from exc
        except httpx.HTTPError as exc:
            raise ManagerError(
                "ALGORITHM_UNAVAILABLE", "远端管理服务无法连接算法容器", 502
            ) from exc
        finally:
            if input_path is not None:
                input_path.unlink(missing_ok=True)

    return router
