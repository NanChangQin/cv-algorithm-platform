from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field

from ....core.container import ApplicationContainer
from ....dependencies import get_container
from ....core.request_context import project_id_from
from ...tasks.api.schemas import TaskResponse
from ...tasks.application.service import TaskService
from ..application.service import ComparisonService

router = APIRouter(prefix="/comparisons", tags=["comparisons"])


class CreateComparisonRequest(BaseModel):
    asset_id: UUID
    algorithm_version_ids: list[UUID] = Field(min_length=2, max_length=8)
    parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ComparisonResponse(BaseModel):
    id: UUID
    asset_id: UUID
    tasks: list[TaskResponse]
    created_at: datetime


def get_service(
    request: Request,
    container: ApplicationContainer = Depends(get_container),
) -> ComparisonService:
    tasks = TaskService(
        container.tasks,
        container.algorithms,
        container.assets,
        container.task_queue,
        project_id_from(request),
        request.state.session.username,
    )
    return ComparisonService(
        container.comparisons,
        tasks,
        project_id_from(request),
        request.state.session.username,
    )


def to_response(comparison, container: ApplicationContainer) -> ComparisonResponse:
    tasks = [container.tasks.get(task_id) for task_id in comparison.task_ids]
    return ComparisonResponse(
        id=comparison.id,
        asset_id=comparison.asset_id,
        tasks=[TaskResponse.model_validate(task) for task in tasks if task is not None],
        created_at=comparison.created_at,
    )


@router.post("", response_model=ComparisonResponse, status_code=status.HTTP_201_CREATED)
def create_comparison(
    payload: CreateComparisonRequest,
    service: ComparisonService = Depends(get_service),
    container: ApplicationContainer = Depends(get_container),
) -> ComparisonResponse:
    comparison = service.create(
        payload.asset_id,
        payload.algorithm_version_ids,
        payload.parameters,
    )
    return to_response(comparison, container)


@router.get("", response_model=list[ComparisonResponse])
def list_comparisons(
    limit: int = Query(default=20, ge=1, le=100),
    service: ComparisonService = Depends(get_service),
    container: ApplicationContainer = Depends(get_container),
) -> list[ComparisonResponse]:
    recent = sorted(service.list(), key=lambda item: item.created_at, reverse=True)[:limit]
    return [to_response(item, container) for item in recent]


@router.get("/{comparison_id}", response_model=ComparisonResponse)
def get_comparison(
    comparison_id: UUID,
    service: ComparisonService = Depends(get_service),
    container: ApplicationContainer = Depends(get_container),
) -> ComparisonResponse:
    return to_response(service.get(comparison_id), container)
