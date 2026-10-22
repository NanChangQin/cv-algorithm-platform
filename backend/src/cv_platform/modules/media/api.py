from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from ...core.request_context import project_id_from
from ...dependencies import get_container
from ..assets.application.service import AssetService
from ..tasks.application.service import TaskService
from .domain import MediaSourceType
from .runs import MediaInferenceRun, MediaRunStatus

router = APIRouter(prefix="/media-sources", tags=["media"])


class CreateMediaSourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    source_type: MediaSourceType
    uri: str = Field(min_length=1, max_length=2048)


class MediaSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    source_type: MediaSourceType
    uri: str
    enabled: bool
    created_at: datetime
    owner_id: str
    project_id: UUID


class ExtractRequest(BaseModel):
    interval_seconds: float = Field(default=1, gt=0, le=60)
    max_frames: int = Field(default=100, ge=1, le=10_000)


class CreateMediaRunRequest(ExtractRequest):
    algorithm_version_id: UUID
    parameters: dict[str, object] = Field(default_factory=dict)


class MediaRunResponse(BaseModel):
    id: UUID
    source_id: UUID
    algorithm_version_id: UUID
    status: MediaRunStatus
    frame_task_ids: list[UUID]
    created_at: datetime
    updated_at: datetime
    error_message: str | None
    project_id: UUID


def task_service(container, project_id: UUID, actor: str = "system") -> TaskService:
    return TaskService(
        container.tasks,
        container.algorithms,
        container.assets,
        container.task_queue,
        project_id,
        actor,
    )


def asset_service(container, project_id: UUID, actor: str) -> AssetService:
    return AssetService(
        container.assets,
        container.asset_storage,
        project_id,
        actor,
    )


@router.post("", response_model=MediaSourceResponse, status_code=201)
def create_source(
    payload: CreateMediaSourceRequest,
    request: Request,
    container=Depends(get_container),
):
    return container.media_sources.create(
        payload.name,
        payload.source_type,
        payload.uri,
        project_id_from(request),
        request.state.session.username,
    )


@router.get("", response_model=list[MediaSourceResponse])
def list_sources(request: Request, container=Depends(get_container)):
    return container.media_sources.list(project_id_from(request))


@router.post("/upload", response_model=MediaSourceResponse, status_code=201)
def upload_video(
    request: Request,
    file: UploadFile = File(...),
    container=Depends(get_container),
):
    uri, _, _ = container.video_storage.store(
        file.file,
        file.filename or "",
        file.content_type,
    )
    return container.media_sources.create(
        file.filename or "video",
        MediaSourceType.VIDEO,
        uri,
        project_id_from(request),
        request.state.session.username,
    )


@router.get("/runs", response_model=list[MediaRunResponse])
def list_media_runs(request: Request, container=Depends(get_container)):
    project_id = project_id_from(request)
    service = task_service(container, project_id, request.state.session.username)
    return [
        container.media_runs.refresh(run.id, service)
        for run in container.media_runs.list(project_id)
    ]


@router.delete("/{source_id}", status_code=204)
def delete_source(
    source_id: UUID,
    request: Request,
    container=Depends(get_container),
) -> Response:
    container.media_sources.delete(source_id, project_id_from(request))
    return Response(status_code=204)


@router.post("/{source_id}/extract")
def extract_frames(
    source_id: UUID,
    payload: ExtractRequest,
    request: Request,
    container=Depends(get_container),
) -> list[dict[str, object]]:
    source = container.media_sources.get(source_id, project_id_from(request))
    return container.media_worker.extract(
        source.uri,
        payload.interval_seconds,
        payload.max_frames,
    )


@router.post("/{source_id}/runs", response_model=MediaRunResponse, status_code=201)
def create_media_run(
    source_id: UUID,
    payload: CreateMediaRunRequest,
    request: Request,
    container=Depends(get_container),
) -> MediaInferenceRun:
    return container.media_runs.create(
        source_id,
        payload.algorithm_version_id,
        payload.interval_seconds,
        payload.max_frames,
        payload.parameters,
        task_service(
            container,
            project_id_from(request),
            request.state.session.username,
        ),
        asset_service(
            container,
            project_id_from(request),
            request.state.session.username,
        ),
        project_id_from(request),
    )


@router.get("/runs/{run_id}", response_model=MediaRunResponse)
def get_media_run(
    run_id: UUID,
    request: Request,
    container=Depends(get_container),
) -> MediaInferenceRun:
    project_id = project_id_from(request)
    container.media_runs.get(run_id, project_id)
    return container.media_runs.refresh(
        run_id,
        task_service(container, project_id, request.state.session.username),
    )
