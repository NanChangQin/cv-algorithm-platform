from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from cv_algorithm_sdk.runtime_contract import get_contract, normalize_parameters
from cv_algorithm_sdk.training_sources import TrainingSource
from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ...core.config import get_settings
from ...core.container import ApplicationContainer
from ...core.errors import ApplicationError
from ...core.request_context import project_id_from
from ...dependencies import get_container
from ..algorithms.api.schemas import AlgorithmResponse
from .datasets import SplitConfig
from .domain import TRAINING_MODELS, TrainingDataset, TrainingRun, TrainingStatus
from .feedback import DatasetFeedbackRequest, feedback_to_dataset
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
        container.algorithms,
        container.model_store,
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
    family_id: UUID
    version: int
    parent_id: UUID | None
    sha256: str
    source_sha256: str | None
    split_config: dict[str, Any]

    @classmethod
    def of(cls, dataset: TrainingDataset) -> DatasetResponse:
        return cls(
            id=dataset.id, name=dataset.name, format=dataset.format,
            class_names=list(dataset.class_names), stats=dataset.stats,
            size_bytes=dataset.size_bytes, created_by=dataset.created_by,
            created_at=dataset.created_at,
            family_id=dataset.family_id or dataset.id, version=dataset.version,
            parent_id=dataset.parent_id, sha256=dataset.sha256,
            source_sha256=dataset.source_sha256, split_config=dataset.split_config,
        )


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["train", "val"] = "train"

    name: str = Field(default="", max_length=120)
    dataset_id: UUID
    model: str = Field(default="custom.pt", min_length=1, max_length=64)
    source: TrainingSource = Field(default_factory=TrainingSource)
    node_id: str = Field(min_length=1, max_length=64)
    gpu_device_ids: list[str] = Field(default_factory=list, max_length=8)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("gpu_device_ids")
    @classmethod
    def unique_gpus(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("GPU 编号不能重复")
        return value

    @model_validator(mode="after")
    def validate_parameters(self):
        normalize_parameters(self.operation, self.parameters)
        if self.operation == "val" and self.source.mode == "resume":
            raise ValueError("独立验证使用模型权重，不支持断点续训")
        return self


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


@router.get("/sources")
def list_sources(service: TrainingService = Depends(get_service)):
    return service.list_sources()


@router.get("/weights")
def list_weights(service: TrainingService = Depends(get_service)):
    return service.weights.list()


@router.post("/weights", status_code=201)
def upload_weight(file: UploadFile = File(...), service: TrainingService = Depends(get_service)):
    return service.import_weight(file.file, file.filename or "")


@router.delete("/weights/{weight_id}", status_code=204)
def delete_weight(weight_id: UUID, service: TrainingService = Depends(get_service)):
    service.weights.delete(weight_id)
    return Response(status_code=204)


@router.get("/parameter-contract")
def parameter_contract(
    mode: Literal["train", "val", "predict", "export", "track", "benchmark", "tune"] = "train",
) -> dict[str, Any]:
    return get_contract(mode)


@router.get("/datasets", response_model=list[DatasetResponse])
def list_datasets(service: TrainingService = Depends(get_service)) -> list[DatasetResponse]:
    return [DatasetResponse.of(item) for item in service.list_datasets()]


@router.post("/datasets", response_model=DatasetResponse, status_code=201)
def import_dataset(
    file: UploadFile = File(...),
    name: str = Form(default="", max_length=120),
    split_config: str = Form(default="{}", max_length=4000),
    parent_id: Annotated[UUID | None, Form()] = None,
    service: TrainingService = Depends(get_service),
) -> DatasetResponse:
    try:
        config = SplitConfig.model_validate_json(split_config)
    except ValidationError as exc:
        raise ApplicationError("INPUT_INVALID", f"划分配置不合法：{exc}", 422) from exc
    return DatasetResponse.of(service.import_dataset(
        file.file, file.filename or "", name, config, parent_id,
    ))


@router.post("/datasets/{dataset_id}/versions", response_model=DatasetResponse, status_code=201)
def new_dataset_version(
    dataset_id: UUID, config: SplitConfig, service: TrainingService = Depends(get_service),
) -> DatasetResponse:
    return DatasetResponse.of(service.new_version(dataset_id, config))


@router.post("/datasets/{dataset_id}/feedback", response_model=DatasetResponse, status_code=201)
def dataset_feedback(
    dataset_id: UUID,
    payload: DatasetFeedbackRequest,
    request: Request,
    service: TrainingService = Depends(get_service),
    container: ApplicationContainer = Depends(get_container),
) -> DatasetResponse:
    return DatasetResponse.of(
        feedback_to_dataset(
            service,
            dataset_id,
            payload,
            tasks=container.tasks,
            assets=container.assets,
            asset_storage=container.asset_storage,
            project_id=project_id_from(request),
        )
    )


@router.get("/datasets/{dataset_id}/images")
def dataset_images(
    dataset_id: UUID, split: Literal["train", "val", "test"] | None = None,
    offset: int = Query(default=0, ge=0), limit: int = Query(default=24, ge=1, le=100),
    service: TrainingService = Depends(get_service),
) -> dict[str, Any]:
    return service.dataset_images(dataset_id, split, offset, limit)


@router.get("/datasets/{dataset_id}/images/{image_id}/preview")
def dataset_preview(
    dataset_id: UUID, image_id: int, service: TrainingService = Depends(get_service),
) -> Response:
    return Response(service.dataset_preview(dataset_id, image_id), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})


@router.get("/datasets/{dataset_id}/report")
def dataset_report(
    dataset_id: UUID, service: TrainingService = Depends(get_service),
) -> dict[str, Any]:
    return service.dataset_report(dataset_id)


@router.get("/datasets/{dataset_id}/archive", response_class=FileResponse)
def dataset_archive(
    dataset_id: UUID, source: bool = False, service: TrainingService = Depends(get_service),
):
    return FileResponse(service.dataset_archive(dataset_id, source=source),
                        media_type="application/zip", filename=f"dataset-{dataset_id}.zip")


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
        parameters=payload.parameters,
        node_id=payload.node_id,
        gpu_device_ids=tuple(payload.gpu_device_ids),
        source=payload.source,
        operation=payload.operation,
    )
    return run_response(run)


@router.get("/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: UUID, service: TrainingService = Depends(get_service)) -> RunResponse:
    return run_response(service.get_run(run_id))


@router.get("/runs/{run_id}/evaluation")
def evaluation_report(run_id: UUID, service: TrainingService = Depends(get_service)):
    return service.evaluation_report(run_id)


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
