import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from .api import build_router, validate_algorithm_image
from .application.errors import ManagerError
from .application.service import InstanceService
from .config import get_settings
from .infrastructure.docker_runtime import DockerContainerRuntime
from .infrastructure.image_builder import ControlledImageBuilder
from .infrastructure.memory_repository import InMemoryInstanceRepository
from .training import TrainingService, build_training_router

logger = logging.getLogger(__name__)


@lru_cache
def get_service() -> InstanceService:
    settings = get_settings()
    runtime = DockerContainerRuntime.from_environment(
        settings.docker_network,
        settings.host_data_root,
        settings.host_model_root,
        settings.health_timeout_seconds,
        gpu_access_mode=settings.gpu_access_mode,
    )
    return InstanceService(InMemoryInstanceRepository(), runtime)


@lru_cache
def get_training() -> TrainingService:
    import docker

    settings = get_settings()
    return TrainingService(
        docker.from_env(),
        settings.data_root,
        settings.host_data_root,
        gpu_access_mode=settings.gpu_access_mode,
        network=settings.training_network,
        shm_mb=settings.training_shm_mb,
        max_dataset_bytes=settings.max_dataset_bytes,
        max_dataset_files=settings.max_dataset_files,
    )


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        service = get_service()
        stop_event = asyncio.Event()

        async def reclaim_loop() -> None:
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=60)
                except TimeoutError:
                    await asyncio.to_thread(
                        service.reclaim_idle,
                        settings.idle_timeout_seconds,
                    )

        async def health_loop() -> None:
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=settings.health_monitor_seconds,
                    )
                except TimeoutError:
                    await asyncio.to_thread(
                        service.monitor_health,
                        settings.health_failure_threshold,
                    )

        tasks = [asyncio.create_task(reclaim_loop()), asyncio.create_task(health_loop())]
        try:
            yield
        finally:
            stop_event.set()
            await asyncio.gather(*tasks)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        redoc_url=None,
        lifespan=lifespan,
    )
    token = settings.api_token.get_secret_value() if settings.api_token else ""
    if token:
        expected = f"Bearer {token}".encode()

        @app.middleware("http")
        async def require_api_token(request: Request, call_next):
            supplied = request.headers.get("Authorization", "").encode()
            if request.url.path != "/health" and not hmac.compare_digest(supplied, expected):
                return JSONResponse(
                    status_code=401,
                    content={
                        "code": "MANAGER_AUTH_REQUIRED",
                        "message": "缺少或错误的管理服务访问令牌",
                    },
                )
            return await call_next(request)
    else:
        logger.warning(
            "ALGORITHM_MANAGER_API_TOKEN 未设置：管理接口不做鉴权，只应在可信网络内访问"
        )

    app.include_router(build_router(get_service), prefix="/api/v1")
    app.include_router(build_training_router(get_training), prefix="/api/v1")

    class BuildImageRequest(BaseModel):
        package_path: str
        image: str
        framework: str

        @field_validator("image")
        @classmethod
        def check_image(cls, value: str) -> str:
            return validate_algorithm_image(value)

    @app.post("/api/v1/images/build", tags=["images"])
    def build_image(payload: BuildImageRequest) -> dict[str, object]:
        import docker

        try:
            builder = ControlledImageBuilder(
                docker.from_env(),
                settings.package_root,
                settings.build_network,
            )
            digest, logs = builder.build(payload.package_path, payload.image, payload.framework)
        except Exception as exc:
            raise ManagerError("BUILD_FAILED", "算法镜像构建失败", 502) from exc
        return {"image": payload.image, "digest": digest, "logs": logs}

    @app.get("/api/v1/images/inspect", tags=["images"])
    def inspect_image(image: str) -> dict[str, object]:
        import docker
        from docker.errors import ImageNotFound

        try:
            validate_algorithm_image(image)
        except ValueError as exc:
            raise ManagerError("INPUT_INVALID", str(exc)) from exc
        try:
            found = docker.from_env().images.get(image)
        except ImageNotFound:
            return {"image": image, "exists": False}
        except Exception as exc:
            raise ManagerError("DOCKER_UNAVAILABLE", "无法读取节点镜像列表", 502) from exc
        return {
            "image": image,
            "exists": True,
            "id": found.id,
            "size": found.attrs.get("Size"),
            "created": found.attrs.get("Created"),
        }

    @app.delete("/api/v1/images", status_code=204, tags=["images"])
    def remove_image(image: str) -> Response:
        import docker

        try:
            ControlledImageBuilder(
                docker.from_env(),
                settings.package_root,
                settings.build_network,
            ).remove(image)
        except Exception as exc:
            raise ManagerError("IMAGE_REMOVE_FAILED", "算法镜像删除失败", 502) from exc
        return Response(status_code=204)

    @app.exception_handler(ManagerError)
    async def manager_error_handler(_: Request, exc: ManagerError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/system/gpus", tags=["system"])
    def gpus(service: InstanceService = Depends(get_service)) -> list[dict[str, object]]:
        return service.list_gpus()

    @app.post("/api/v1/system/reclaim-idle", tags=["system"])
    def reclaim_idle(
        service: InstanceService = Depends(get_service),
    ) -> dict[str, object]:
        stopped = service.reclaim_idle(settings.idle_timeout_seconds)
        return {"stopped_instance_ids": stopped}

    return app


app = create_app()
