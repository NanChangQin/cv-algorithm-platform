"""Independent export jobs using the existing durable job coordinator/storage."""

import json
from uuid import UUID

from cv_algorithm_sdk.exports import ExportVerification
from cv_algorithm_sdk.runtime_contract import normalize_parameters
from cv_algorithm_sdk.training_sources import TrainingSource
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...core.config import get_settings
from ...core.container import ApplicationContainer
from ...core.errors import ApplicationError
from ...core.request_context import project_id_from
from ...dependencies import get_container
from ..algorithms.api.schemas import AlgorithmResponse
from .api import RunResponse, get_service, run_response
from .export_publish import publish_export
from .service import TrainingService

router = APIRouter(prefix="/exports", tags=["exports"])


class CreateExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=120)
    dataset_id: UUID
    node_id: str = Field(min_length=1, max_length=64)
    model: str = Field(default="custom.pt", max_length=64)
    source: TrainingSource = Field(default_factory=TrainingSource)
    parameters: dict = Field(default_factory=dict)
    verification: ExportVerification = Field(default_factory=ExportVerification)

    @model_validator(mode="after")
    def validate_export(self):
        normalize_parameters("export", self.parameters)
        if self.source.mode == "resume":
            raise ValueError("导出选择 best/last 或模型权重，不支持 resume")
        return self


def export_job(service: TrainingService, job_id: UUID):
    run = service.get_run(job_id)
    if (run.progress or {}).get("operation") != "export":
        raise ApplicationError("EXPORT_NOT_FOUND", "导出任务不存在", 404)
    return run


@router.post("", response_model=RunResponse, status_code=201)
def create(payload: CreateExportRequest, service: TrainingService = Depends(get_service)):
    return run_response(
        service.create_run(
            name=payload.name or "ONNX 导出与验证",
            dataset_id=payload.dataset_id,
            node_id=payload.node_id,
            gpu_device_ids=(),
            model=payload.model,
            source=payload.source,
            operation="export",
            parameters=payload.parameters,
            verification=payload.verification,
        )
    )


@router.get("", response_model=list[RunResponse])
def jobs(service: TrainingService = Depends(get_service)):
    return [run_response(run) for run in service.list_runs(operation="export")]


class PublishExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=3, max_length=64)
    name: str = Field(default="", max_length=128)
    version: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=1000)


@router.post("/{job_id}/publish", response_model=AlgorithmResponse, status_code=201)
def publish(job_id: UUID, payload: PublishExportRequest, request: Request,
            container: ApplicationContainer = Depends(get_container)):
    return AlgorithmResponse.from_entity(publish_export(
        container, project_id_from(request), request.state.session.username,
        job_id, payload, get_settings().onnx_inference_image))


@router.get("/{job_id}", response_model=RunResponse)
def get(job_id: UUID, service: TrainingService = Depends(get_service)):
    return run_response(export_job(service, job_id))


@router.post("/{job_id}/stop", response_model=RunResponse)
def stop(job_id: UUID, service: TrainingService = Depends(get_service)):
    export_job(service, job_id)
    return run_response(service.stop_run(job_id))


@router.delete("/{job_id}", status_code=204)
def remove(job_id: UUID, service: TrainingService = Depends(get_service)):
    export_job(service, job_id)
    service.delete_run(job_id)
    return Response(status_code=204)


@router.get("/{job_id}/report")
def report(job_id: UUID, service: TrainingService = Depends(get_service)):
    export_job(service, job_id)
    path = service.artifact(job_id, "export-report.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ApplicationError("REPORT_INVALID", "导出报告读取失败", 409) from exc


@router.get("/{job_id}/logs")
def logs(
    job_id: UUID,
    tail: int = Query(default=300, ge=1, le=2000),
    service: TrainingService = Depends(get_service),
):
    export_job(service, job_id)
    return service.logs(job_id, tail)


@router.get("/{job_id}/archive")
def archive(job_id: UUID, service: TrainingService = Depends(get_service)):
    export_job(service, job_id)
    return FileResponse(
        service.archive(job_id), media_type="application/zip", filename=f"export-{job_id}.zip"
    )


@router.get("/{job_id}/artifacts/{name:path}")
def artifact(job_id: UUID, name: str, service: TrainingService = Depends(get_service)):
    export_job(service, job_id)
    path = service.artifact(job_id, name)
    return FileResponse(path, filename=path.name)
