"""Disposable Manager without discovery or production-wide reconciliation loops."""

import os
from pathlib import Path

import docker
from algorithm_manager.api import build_router, validate_algorithm_image
from algorithm_manager.application.errors import ManagerError
from algorithm_manager.application.service import InstanceService
from algorithm_manager.infrastructure.docker_runtime import DockerContainerRuntime
from algorithm_manager.infrastructure.memory_repository import (
    InMemoryInstanceRepository,
)
from algorithm_manager.transfer import TransferService, build_transfer_router
from fastapi import FastAPI
from fastapi.responses import JSONResponse

host = Path(os.environ["REGRESSION_HOST_ROOT"])
assert str(host) == "/tmp/cv-runtime-20261023"


class IsolatedRuntime(DockerContainerRuntime):
    def discover(self):
        return []


client = docker.from_env()
runtime = IsolatedRuntime(
    client, "cv-runtime-regression-20261023", host / "data", host / "models"
)
service = InstanceService(InMemoryInstanceRepository(), runtime)
transfer = TransferService(
    client, Path("/data"), Path("/packages"), Path("/models"), validate_algorithm_image
)
app = FastAPI()
app.include_router(build_router(lambda: service), prefix="/api/v1")
app.include_router(build_transfer_router(lambda: transfer), prefix="/api/v1")


@app.exception_handler(ManagerError)
async def error_handler(request, error):
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "message": error.message},
    )


@app.get("/api/v1/system/gpus")
def gpus():
    return []


@app.get("/api/v1/images/inspect")
def inspect_image(image: str):
    validate_algorithm_image(image)
    try:
        found = client.images.get(image)
    except docker.errors.ImageNotFound:
        return {"exists": False}
    return {"exists": True, "id": found.id, "size": found.attrs.get("Size")}


@app.get("/health")
def health():
    return {"ready": True}
