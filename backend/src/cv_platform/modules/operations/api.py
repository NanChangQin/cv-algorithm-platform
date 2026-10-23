from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from ...core.request_context import project_id_from
from ...dependencies import get_container
from ..algorithms.api.schemas import AlgorithmResponse
from .service import AutoscalingPolicy

router = APIRouter(prefix="/operations", tags=["operations"])


class TrafficRequest(BaseModel):
    weights: dict[UUID, int]


class ScalingPolicyRequest(BaseModel):
    min_replicas: int = Field(default=0, ge=0, le=100)
    max_replicas: int = Field(default=1, ge=1, le=100)
    target_concurrency: int = Field(default=1, ge=1, le=1000)
    idle_seconds: int = Field(default=1800, ge=30, le=86400)


class ScalingPolicyResponse(BaseModel):
    algorithm_version_id: UUID
    min_replicas: int
    max_replicas: int
    target_concurrency: int
    idle_seconds: int
    updated_at: datetime


@router.post("/traffic", response_model=list[AlgorithmResponse])
def set_traffic(
    payload: TrafficRequest,
    request: Request,
    container=Depends(get_container),
):
    return [
        AlgorithmResponse.from_entity(item)
        for item in container.operations.set_traffic(
            payload.weights,
            project_id_from(request),
        )
    ]


@router.put("/autoscaling/{algorithm_id}", response_model=ScalingPolicyResponse)
def set_autoscaling(
    algorithm_id: UUID,
    payload: ScalingPolicyRequest,
    request: Request,
    container=Depends(get_container),
) -> AutoscalingPolicy:
    return container.operations.set_policy(
        algorithm_id,
        **payload.model_dump(),
        project_id=project_id_from(request),
    )


@router.get("/autoscaling", response_model=list[ScalingPolicyResponse])
def list_autoscaling(request: Request, container=Depends(get_container)):
    return container.operations.list_policies(project_id_from(request))


@router.post("/autoscaling/actions/reconcile")
def reconcile_autoscaling(request: Request, container=Depends(get_container)):
    return container.operations.reconcile(project_id_from(request))


@router.get("/metrics")
def metrics(
    request: Request,
    deployment_id: UUID | None = None,
    window_seconds: int = Query(default=60, ge=5, le=86400),
    container=Depends(get_container),
):
    return container.operations.metrics(
        project_id_from(request),
        deployment_id=deployment_id,
        window_seconds=window_seconds,
    )
