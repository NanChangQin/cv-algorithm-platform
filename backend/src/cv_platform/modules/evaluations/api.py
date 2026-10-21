from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from cv_algorithm_sdk import ResultType
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from ...core.container import ApplicationContainer
from ...core.request_context import project_id_from
from ...dependencies import get_container
from ..tasks.application.service import TaskService
from .domain import EvaluationDataset, EvaluationRun
from .service import EvaluationService

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


def get_service(
    request: Request,
    container: ApplicationContainer = Depends(get_container),
) -> EvaluationService:
    project_id = project_id_from(request)
    actor = request.state.session.username
    tasks = TaskService(
        container.tasks, container.algorithms, container.assets, container.task_queue,
        project_id, actor,
    )
    return EvaluationService(
        container.evaluations, tasks, container.tasks, container.assets, container.algorithms,
        project_id, actor,
    )


class DatasetResponse(BaseModel):
    id: UUID
    name: str
    task_type: str
    item_count: int
    created_by: str
    created_at: datetime

    @classmethod
    def of(cls, dataset: EvaluationDataset) -> DatasetResponse:
        return cls(
            id=dataset.id, name=dataset.name, task_type=dataset.task_type,
            item_count=len(dataset.items), created_by=dataset.created_by,
            created_at=dataset.created_at,
        )


class DatasetCreatedResponse(DatasetResponse):
    unmatched_files: list[str]
    unlabeled_images: list[str]


class CreateDatasetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    task_type: ResultType
    annotations_filename: str = Field(min_length=1, max_length=255)
    annotations: str = Field(min_length=1, max_length=20_000_000)
    asset_ids: list[UUID] = Field(min_length=1, max_length=5000)


class StartRunRequest(BaseModel):
    dataset_id: UUID
    algorithm_version_ids: list[UUID] = Field(min_length=1, max_length=10)
    parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)


class RunResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    dataset_name: str
    task_type: str
    algorithm_version_ids: list[UUID]
    task_count: int
    finished: bool
    created_by: str
    created_at: datetime


class RunReportResponse(BaseModel):
    run: RunResponse
    report: dict[str, Any]


def run_response(
    run: EvaluationRun, datasets: dict[UUID, EvaluationDataset], finished: bool
) -> RunResponse:
    dataset = datasets.get(run.dataset_id)
    return RunResponse(
        id=run.id, dataset_id=run.dataset_id,
        dataset_name=dataset.name if dataset else "已删除的评测集",
        task_type=dataset.task_type if dataset else "",
        algorithm_version_ids=list(run.algorithm_version_ids), task_count=len(run.task_ids),
        finished=finished, created_by=run.created_by, created_at=run.created_at,
    )


@router.get("/datasets", response_model=list[DatasetResponse])
def list_datasets(service: EvaluationService = Depends(get_service)) -> list[DatasetResponse]:
    return [DatasetResponse.of(item) for item in service.list_datasets()]


@router.post(
    "/datasets", response_model=DatasetCreatedResponse, status_code=status.HTTP_201_CREATED
)
def create_dataset(
    payload: CreateDatasetRequest, service: EvaluationService = Depends(get_service)
) -> DatasetCreatedResponse:
    dataset, unmatched, unlabeled = service.create_dataset(
        payload.name, payload.task_type.value, payload.annotations_filename,
        payload.annotations, payload.asset_ids,
    )
    return DatasetCreatedResponse(
        **DatasetResponse.of(dataset).model_dump(),
        unmatched_files=unmatched[:200], unlabeled_images=unlabeled[:200],
    )


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(dataset_id: UUID, service: EvaluationService = Depends(get_service)) -> Response:
    service.delete_dataset(dataset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/runs", response_model=list[RunResponse])
def list_runs(service: EvaluationService = Depends(get_service)) -> list[RunResponse]:
    datasets = {item.id: item for item in service.list_datasets()}
    return [run_response(run, datasets, service.is_finished(run)) for run in service.list_runs()]


@router.post("/runs", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
def start_run(
    payload: StartRunRequest, service: EvaluationService = Depends(get_service)
) -> RunResponse:
    run = service.start_run(payload.dataset_id, payload.algorithm_version_ids, payload.parameters)
    return run_response(run, {run.dataset_id: service.get_dataset(run.dataset_id)}, False)


@router.get("/runs/{run_id}", response_model=RunReportResponse)
def get_run(run_id: UUID, service: EvaluationService = Depends(get_service)) -> RunReportResponse:
    run = service.get_run(run_id)
    report = service.report(run)
    datasets = {item.id: item for item in service.list_datasets()}
    return RunReportResponse(
        run=run_response(run, datasets, bool(report.get("finished"))), report=report
    )
