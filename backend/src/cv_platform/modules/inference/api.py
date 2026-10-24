import io
import re
import tempfile
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from ...core.errors import ApplicationError
from ...core.request_context import project_id_from
from ...dependencies import get_container
from .reports import export_file, snapshot
from .service import TERMINAL

router = APIRouter(prefix="/inference", tags=["inference"])


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=128)
    kind: Literal["images", "video", "browser", "camera", "rtsp"] = "images"
    algorithm_version_id: UUID
    parameters: dict = Field(default_factory=dict)
    asset_ids: list[UUID] = Field(default_factory=list, max_length=100)
    source_id: UUID | None = None
    node_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    gpu_device_ids: list[str] = Field(default_factory=list, max_length=1)
    deployment_id: UUID | None = None
    tracking: bool = False
    interval_seconds: float = Field(default=1, ge=0, le=60)
    max_frames: int = Field(default=1000, ge=1, le=10000)
    duration_seconds: int = Field(default=3600, ge=0, le=28800)
    reconnect_attempts: int = Field(default=3, ge=0, le=5)
    save_video: bool = True
    save_recording: bool = False
    storage_limit_mb: int = Field(default=512, ge=32, le=2048)
    retention_days: int = Field(default=7, ge=1, le=90)
    retry_of: UUID | None = None


@router.post("/sessions", status_code=201)
def create(payload: CreateSession, request: Request, container=Depends(get_container)):
    if any(not s.isascii() or not s.isdigit() for s in payload.gpu_device_ids):
        raise ApplicationError("GPU_INVALID", "GPU 编号必须是数字")
    if payload.node_id:
        container.algorithm_manager.validate_node(payload.node_id)
    project = project_id_from(request)
    if payload.retry_of:
        container.inference.store.get(payload.retry_of, project)
    return container.inference.create(
        payload.model_dump(mode="json"), project, request.state.session.username, container
    )


@router.get("/sessions")
def list_sessions(
    request: Request,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    container=Depends(get_container),
):
    return container.inference.store.list(project_id_from(request), limit, offset)


@router.get("/cameras")
def cameras(request: Request, container=Depends(get_container)):
    project_id_from(request)
    return container.inference.worker("GET", "/sessions/devices/cameras")


@router.get("/sessions/{session_id}")
def get(session_id: UUID, request: Request, container=Depends(get_container)):
    return container.inference.store.get(session_id, project_id_from(request))


@router.post("/sessions/{session_id}/stop")
def stop(session_id: UUID, request: Request, container=Depends(get_container)):
    return container.inference.stop(session_id, project_id_from(request))


@router.post("/sessions/{session_id}/retry", status_code=201)
def retry(session_id: UUID, request: Request, container=Depends(get_container)):
    service = container.inference
    project = project_id_from(request)
    state = service.store.get(session_id, project)
    if state["status"] not in TERMINAL:
        raise ApplicationError("SESSION_ACTIVE", "请先停止当前会话", 409)
    config = dict(state["config"], retry_of=str(session_id), name=state["name"] + " · 重试")
    if state["kind"] == "browser":
        raise ApplicationError("BROWSER_REQUIRED", "浏览器摄像头请回推理中心重新授权采集")
    if state["kind"] == "images":
        completed = {
            f["asset_id"]
            for f in service.store.frames(session_id, 10000)
            if f["status"] == "completed"
        }
        config["asset_ids"] = [key for key in config["asset_ids"] if key not in completed]
        if not config["asset_ids"]:
            raise ApplicationError("NO_FAILED_INPUTS", "没有失败或未处理的图片")
    return service.create(config, project, request.state.session.username, container)


@router.post("/sessions/{session_id}/frames", status_code=202)
async def upload_frame(
    session_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    container=Depends(get_container),
):
    # Bound bytes before decoding; never read arbitrarily large uploads into memory.
    data = await file.read(8 * 1024 * 1024 + 1)
    return container.inference.browser_frame(session_id, project_id_from(request), data)


@router.get("/sessions/{session_id}/frames")
def list_frames(
    session_id: UUID,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    container=Depends(get_container),
):
    state = container.inference.store.get(session_id, project_id_from(request))
    return {
        "items": container.inference.store.frames(session_id, limit, offset),
        "total": state["processed"],
    }


@router.get("/sessions/{session_id}/report")
def report(
    session_id: UUID,
    request: Request,
    start: float | None = Query(None, ge=0),
    end: float | None = Query(None, ge=0),
    label: str | None = Query(None, max_length=128),
    confidence: float = Query(0, ge=0, le=1),
    container=Depends(get_container),
):
    if start is not None and end is not None and end <= start:
        raise ApplicationError("TIME_RANGE_INVALID", "结束时间必须大于开始时间")
    state = container.inference.store.get(session_id, project_id_from(request))
    return snapshot(container.inference, state, start, end, label, confidence)


@router.get("/sessions/{session_id}/download/{name}")
def export(
    session_id: UUID,
    name: str,
    request: Request,
    start: float | None = Query(None, ge=0),
    end: float | None = Query(None, ge=0),
    label: str | None = Query(None, max_length=128),
    confidence: float = Query(0, ge=0, le=1),
    container=Depends(get_container),
):
    if name not in {
        "report.pdf",
        "report.json",
        "metrics.csv",
        "summary.csv",
        "results.json",
        "classes.png",
        "bundle.zip",
    }:
        raise ApplicationError("DOWNLOAD_NOT_FOUND", "文件不存在", 404)
    data = report(session_id, request, start, end, label, confidence, container)
    with tempfile.NamedTemporaryFile(
        prefix="cv-inference-export-", suffix="-" + name, delete=False
    ) as temporary:
        path = Path(temporary.name)
    try:
        media_type = export_file(container.inference, data, name, path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(
        path,
        media_type=media_type,
        filename=f"inference-{session_id}-{name}",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.get("/sessions/{session_id}/files/{name}")
def file(session_id: UUID, name: str, request: Request, container=Depends(get_container)):
    container.inference.store.get(session_id, project_id_from(request))
    if not re.fullmatch(
        r"(preview\.jpg|result-\d{8}\.(?:jpg|png)|result\.mp4|recording\.mp4)", name
    ):
        raise ApplicationError("FILE_NOT_FOUND", "文件不存在", 404)
    path = container.inference.root / str(session_id) / name
    if name.endswith(".png"):
        source = path.with_suffix(".jpg")
        if source.is_file():
            output = io.BytesIO()
            with Image.open(source) as image:
                image.save(output, format="PNG")
            return Response(
                output.getvalue(),
                media_type="image/png",
                headers={"Content-Disposition": f'attachment; filename="{name}"'},
            )
    if not path.is_file():
        raise ApplicationError("FILE_NOT_FOUND", "产物未生成或已按保留策略清理", 404)
    return FileResponse(path, headers={"Cache-Control": "no-store"})
