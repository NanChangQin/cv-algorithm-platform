from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from ...core.request_context import project_id_from
from ...dependencies import get_container
from ..tasks.application.service import TaskService
from .domain import WorkflowMode, WorkflowNode, WorkflowRunStatus

router = APIRouter(prefix="/workflows", tags=["workflows"])


class WorkflowNodeRequest(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    algorithm_version_id: UUID
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)

    def to_entity(self) -> WorkflowNode:
        return WorkflowNode(
            self.id,
            self.algorithm_version_id,
            self.parameters,
            tuple(self.depends_on),
        )


class CreateWorkflowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    mode: WorkflowMode
    nodes: list[WorkflowNodeRequest]


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    mode: WorkflowMode
    nodes: list[WorkflowNode]
    created_at: datetime
    created_by: str
    project_id: UUID


class StartWorkflowRequest(BaseModel):
    asset_id: UUID


class WorkflowRunResponse(BaseModel):
    id: UUID
    workflow_id: UUID
    asset_id: UUID
    status: WorkflowRunStatus
    node_tasks: dict[str, UUID]
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


@router.post("", response_model=WorkflowResponse, status_code=201)
def create_workflow(
    payload: CreateWorkflowRequest,
    request: Request,
    container=Depends(get_container),
):
    return container.workflows.create(
        payload.name,
        payload.mode,
        [node.to_entity() for node in payload.nodes],
        project_id_from(request),
        request.state.session.username,
    )


@router.get("", response_model=list[WorkflowResponse])
def list_workflows(request: Request, container=Depends(get_container)):
    return container.workflows.list(project_id_from(request))


@router.get("/runs", response_model=list[WorkflowRunResponse])
def list_workflow_runs(request: Request, container=Depends(get_container)):
    return container.workflows.list_runs(project_id_from(request))


@router.post("/{workflow_id}/runs", response_model=WorkflowRunResponse, status_code=201)
def start_workflow(
    workflow_id: UUID,
    payload: StartWorkflowRequest,
    request: Request,
    container=Depends(get_container),
):
    project_id = project_id_from(request)
    return container.workflows.start(
        workflow_id,
        payload.asset_id,
        task_service(container, project_id, request.state.session.username),
        project_id,
    )


@router.get("/runs/{run_id}", response_model=WorkflowRunResponse)
def get_workflow_run(run_id: UUID, request: Request, container=Depends(get_container)):
    return container.workflows.get_run(run_id, project_id_from(request))


@router.post("/runs/{run_id}/cancel", response_model=WorkflowRunResponse)
def cancel_workflow_run(run_id: UUID, request: Request, container=Depends(get_container)):
    project_id = project_id_from(request)
    container.workflows.get_run(run_id, project_id)
    return container.workflows.cancel(
        run_id,
        task_service(container, project_id, request.state.session.username),
    )
