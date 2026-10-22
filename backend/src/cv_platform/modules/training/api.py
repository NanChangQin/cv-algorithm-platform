from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from ...core.config import get_settings
from ...core.container import ApplicationContainer
from ...core.request_context import project_id_from
from ...dependencies import get_container
from ..algorithms.api.schemas import AlgorithmResponse
from .domain import TRAINING_MODELS, TrainingDataset, TrainingRun, TrainingStatus
from .publish import ModelPublisher
from .service import TrainingService

router = APIRouter(prefix="/training", tags=["training"])

# Ultralytics results.csv 中的指标列
PRECISION = "metrics/precision(B)"
RECALL = "metrics/recall(B)"
MAP50 = "metrics/mAP50(B)"
MAP50_95 = "metrics/mAP50-95(B)"


def get_service(
    request: Request,
    container: ApplicationContainer = Depends(get_container),
) -> TrainingService:
    return TrainingService(
        container.training,
        container.training_storage,
        container.algorithm_manager,
        project_id_from(request),
        request.state.session.username,
    )


class ModelResponse(BaseModel):
    key: str
    label: str
    description: str


class DatasetResponse(BaseModel):
    id: UUID
    name: str
    format: str
    class_names: list[str]
    stats: dict[str, Any]
    size_bytes: int
    created_by: str
    created_at: datetime

    @classmethod
    def of(cls, dataset: TrainingDataset) -> DatasetResponse:
        return cls(
            id=dataset.id, name=dataset.name, format=dataset.format,
            class_names=list(dataset.class_names), stats=dataset.stats,
            size_bytes=dataset.size_bytes, created_by=dataset.created_by,
            created_at=dataset.created_at,
        )


class TrainingParameters(BaseModel):
    epochs: int = Field(default=100, ge=1, le=1000)
    batch: int = Field(default=16, ge=-1, le=512)
    imgsz: int = Field(default=640, ge=32, le=2048, multiple_of=32)
    lr0: float = Field(default=0.01, gt=0, le=1)
    optimizer: Literal["auto", "SGD", "Adam", "AdamW"] = "auto"
    patience: int = Field(default=50, ge=0, le=1000)
    workers: int = Field(default=4, ge=0, le=32)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    cos_lr: bool = False

    @field_validator("batch")
    @classmethod
    def check_batch(cls, value: int) -> int:
        if value == 0:
            raise ValueError("batch 为 -1（自动）或正整数")
        return value


class CreateRunRequest(BaseModel):
    name: str = Field(default="", max_length=120)
    dataset_id: UUID
    model: str = Field(min_length=1, max_length=64)
    node_id: str = Field(min_length=1, max_length=64)
    gpu_device_ids: list[str] = Field(default_factory=list, max_length=8)
    parameters: TrainingParameters = Field(default_factory=TrainingParameters)


class BestMetrics(BaseModel):
    epoch: int
    precision: float | None
    recall: float | None
    map50: float | None
    map50_95: float | None


class RunSummaryResponse(BaseModel):
    id: UUID
    name: str
    dataset_id: UUID
    model: str
    parameters: dict[str, Any]
    node_id: str
    gpu_device_ids: list[str]
    status: TrainingStatus
    stage: str | None
    progress: dict[str, Any] | None
    best: BestMetrics | None
    error: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def of(cls, run: TrainingRun, **extra: Any) -> Any:
        return cls(
            id=run.id, name=run.name, dataset_id=run.dataset_id, model=run.model,
            parameters=run.parameters, node_id=run.node_id,
            gpu_device_ids=list(run.gpu_device_ids), status=run.status, stage=run.stage,
            progress=run.progress, best=best_metrics(run.metrics), error=run.error,
            created_by=run.created_by, created_at=run.created_at, updated_at=run.updated_at,
            started_at=run.started_at, finished_at=run.finished_at, **extra,
        )


class RunResponse(RunSummaryResponse):
    metrics: list[dict[str, float]]
    artifacts: dict[str, int]


def best_metrics(rows: tuple[dict[str, float], ...]) -> BestMetrics | None:
    scored = [row for row in rows if MAP50_95 in row]
    if not scored:
        return None
    best = max(scored, key=lambda row: (row[MAP50_95], row.get(MAP50, 0)))
    return BestMetrics(
        epoch=int(best.get("epoch", 0)),
        precision=best.get(PRECISION),
        recall=best.get(RECALL),
        map50=best.get(MAP50),
        map50_95=best.get(MAP50_95),
    )


@router.get("/models", response_model=list[ModelResponse])
def list_models() -> list[ModelResponse]:
    return [ModelResponse(key=item.key, label=item.label, description=item.description)
            for item in TRAINING_MODELS]


@router.get("/datasets", response_model=list[DatasetResponse])
def list_datasets(service: TrainingService = Depends(get_service)) -> list[DatasetResponse]:
    return [DatasetResponse.of(item) for item in service.list_datasets()]


@router.post("/datasets", response_model=DatasetResponse, status_code=201)
def import_dataset(
    file: UploadFile = File(...),
    name: str = Form(default="", max_length=120),
    service: TrainingService = Depends(get_service),
) -> DatasetResponse:
    return DatasetResponse.of(service.import_dataset(file.file, file.filename or "", name))


@router.get("/datasets/{dataset_id}", response_model=DatasetResponse)
def get_dataset(
    dataset_id: UUID, service: TrainingService = Depends(get_service)
) -> DatasetResponse:
    return DatasetResponse.of(service.get_dataset(dataset_id))


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(dataset_id: UUID, service: TrainingService = Depends(get_service)) -> Response:
    service.delete_dataset(dataset_id)
    return Response(status_code=204)


@router.get("/runs", response_model=list[RunSummaryResponse])
def list_runs(service: TrainingService = Depends(get_service)) -> list[RunSummaryResponse]:
    return [RunSummaryResponse.of(item) for item in service.list_runs()]


@router.post("/runs", response_model=RunResponse, status_code=201)
def create_run(
    payload: CreateRunRequest, service: TrainingService = Depends(get_service)
) -> RunResponse:
    run = service.create_run(
        name=payload.name,
        dataset_id=payload.dataset_id,
        model=payload.model,
        parameters=payload.parameters.model_dump(),
        node_id=payload.node_id,
        gpu_device_ids=tuple(payload.gpu_device_ids),
    )
    return run_response(run)


@router.get("/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: UUID, service: TrainingService = Depends(get_service)) -> RunResponse:
    return run_response(service.get_run(run_id))


@router.post("/runs/{run_id}/stop", response_model=RunResponse)
def stop_run(run_id: UUID, service: TrainingService = Depends(get_service)) -> RunResponse:
    return run_response(service.stop_run(run_id))


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(run_id: UUID, service: TrainingService = Depends(get_service)) -> Response:
    service.delete_run(run_id)
    return Response(status_code=204)


class PublishRequest(BaseModel):
    checkpoint: Literal["best", "last"] = "best"
    key: str = Field(min_length=3, max_length=64)
    name: str = Field(default="", max_length=128)
    version: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=1000)


@router.post("/runs/{run_id}/publish", response_model=AlgorithmResponse, status_code=201)
def publish_model(
    run_id: UUID,
    payload: PublishRequest,
    request: Request,
    container: ApplicationContainer = Depends(get_container),
) -> AlgorithmResponse:
    publisher = ModelPublisher(
        container.training, container.training_storage, container.algorithms,
        container.model_store, get_settings().yolo_inference_image,
        project_id_from(request), request.state.session.username,
    )
    return AlgorithmResponse.from_entity(publisher.publish(
        run_id, checkpoint=payload.checkpoint, key=payload.key, name=payload.name,
        version=payload.version, description=payload.description,
    ))


@router.get("/runs/{run_id}/logs", response_model=list[str])
def run_logs(
    run_id: UUID,
    tail: int = 300,
    service: TrainingService = Depends(get_service),
) -> list[str]:
    return service.logs(run_id, min(max(tail, 1), 2000))


@router.get("/runs/{run_id}/archive", response_class=FileResponse)
def download_archive(run_id: UUID, service: TrainingService = Depends(get_service)):
    run = service.get_run(run_id)
    return FileResponse(
        service.archive(run.id),
        media_type="application/zip",
        filename=f"training-{run.id.hex[:8]}.zip",
    )


@router.get("/runs/{run_id}/artifacts/{name:path}", response_class=FileResponse)
def download_artifact(
    run_id: UUID, name: str, service: TrainingService = Depends(get_service)
):
    path = service.artifact(run_id, name)
    media_type = {".png": "image/png", ".jpg": "image/jpeg", ".csv": "text/csv"}.get(
        path.suffix, "application/octet-stream"
    )
    inline = path.suffix in {".png", ".jpg"}
    return FileResponse(
        path,
        media_type=media_type,
        filename=None if inline else f"{run_id.hex[:8]}-{path.name}",
        headers={"Cache-Control": "private, max-age=86400"},
    )


def run_response(run: TrainingRun) -> RunResponse:
    return RunResponse.of(run, metrics=list(run.metrics), artifacts=run.artifacts)
