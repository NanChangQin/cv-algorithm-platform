import json
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from cv_algorithm_sdk import TaskStatus
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from ....core.container import ApplicationContainer
from ....core.errors import ApplicationError
from ....core.request_context import project_id_from
from ....dependencies import get_container
from ..application.service import TaskService
from .schemas import (
    CreateTaskRequest,
    ResultArchiveRequest,
    TaskResponse,
    TaskResultResponse,
    TaskSummaryResponse,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_service(
    request: Request,
    container: ApplicationContainer = Depends(get_container),
) -> TaskService:
    return TaskService(
        container.tasks,
        container.algorithms,
        container.assets,
        container.task_queue,
        project_id_from(request),
        request.state.session.username,
    )


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: CreateTaskRequest,
    request: Request,
    service: TaskService = Depends(get_service),
    container: ApplicationContainer = Depends(get_container),
) -> TaskResponse:
    algorithm_version_id = payload.algorithm_version_id
    if algorithm_version_id is None and payload.algorithm_key is not None:
        routing_key = str(payload.asset_id or payload.asset_uri)
        algorithm_version_id = container.operations.choose_version(
            payload.algorithm_key,
            routing_key,
            project_id_from(request),
        ).id
    if algorithm_version_id is None:
        raise ApplicationError("ALGORITHM_NOT_FOUND", "算法版本不存在", 404)
    if payload.node_id is not None:
        container.algorithm_manager.validate_node(payload.node_id)
    task = service.create_task(
        algorithm_version_id,
        payload.parameters,
        asset_id=payload.asset_id,
        asset_uri=payload.asset_uri,
        node_id=payload.node_id,
        gpu_device_ids=tuple(payload.gpu_device_ids),
    )
    return TaskResponse.model_validate(task)


@router.get("", response_model=list[TaskResponse])
def list_tasks(
    response: Response,
    algorithm_version_id: UUID | None = None,
    asset_id: UUID | None = None,
    task_statuses: list[TaskStatus] = Query(default_factory=list, alias="status"),
    node_id: str | None = Query(default=None, max_length=64),
    created_after: datetime | None = None,
    q: str | None = Query(default=None, max_length=200),
    ids: list[UUID] = Query(default_factory=list, max_length=200),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: TaskService = Depends(get_service),
    container: ApplicationContainer = Depends(get_container),
) -> list[TaskResponse]:
    """status 可重复传多个；不传 limit 时返回全部（兼容旧调用）；总数放在 X-Total-Count 响应头。"""
    text = q.strip() if q else ""
    search = None
    if text:
        nodes = [(node.id, node.name) for node in container.algorithm_manager.list_nodes()]
        search = service.resolve_search(text, nodes)
    tasks, total = service.list_tasks(
        algorithm_version_id=algorithm_version_id,
        asset_id=asset_id,
        statuses=tuple(task_statuses),
        node_id=node_id,
        created_after=created_after,
        ids=tuple(ids),
        search=search,
        limit=limit,
        offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return [TaskResponse.model_validate(task) for task in tasks]


@router.get("/summary", response_model=TaskSummaryResponse)
def task_summary(service: TaskService = Depends(get_service)) -> TaskSummaryResponse:
    counts = service.count_by_status()
    return TaskSummaryResponse(
        total=sum(counts.values()),
        by_status={item.value: counts.get(item, 0) for item in TaskStatus},
    )


@router.post("/results/archive")
def archive_results(
    payload: ResultArchiveRequest,
    service: TaskService = Depends(get_service),
) -> StreamingResponse:
    if len(set(payload.task_ids)) != len(payload.task_ids):
        raise ApplicationError("ARCHIVE_INVALID", "任务 ID 不能重复")
    buffer = BytesIO()
    manifest = []
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for task_id in payload.task_ids:
            task = service.get_task(task_id)
            if task.status is not TaskStatus.COMPLETED or task.result is None:
                raise ApplicationError("RESULT_NOT_READY", f"任务 {task_id} 结果尚未生成", 409)
            result_data = task.result.model_dump(mode="json")
            archive.writestr(
                f"results/{task.id}.json",
                json.dumps(result_data, ensure_ascii=False, indent=2),
            )
            manifest.append(
                {
                    "task_id": str(task.id),
                    "algorithm_version_id": str(task.algorithm_version_id),
                    "asset_id": str(task.asset_id) if task.asset_id else None,
                    "parameters": task.parameters,
                    "result_file": f"results/{task.id}.json",
                }
            )
        archive.writestr(
            "manifest.json",
            json.dumps(
                {"exported_at": datetime.now(UTC).isoformat(), "tasks": manifest},
                ensure_ascii=False,
                indent=2,
            ),
        )
    buffer.seek(0)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="cv-results-{stamp}.zip"'},
    )


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: UUID, service: TaskService = Depends(get_service)) -> TaskResponse:
    return TaskResponse.model_validate(service.get_task(task_id))


@router.post("/{task_id}/cancel", response_model=TaskResponse)
def cancel_task(task_id: UUID, service: TaskService = Depends(get_service)) -> TaskResponse:
    return TaskResponse.model_validate(service.cancel(task_id))


@router.post("/{task_id}/retry", response_model=TaskResponse, status_code=201)
def retry_task(task_id: UUID, service: TaskService = Depends(get_service)) -> TaskResponse:
    return TaskResponse.model_validate(service.retry(task_id))


@router.get("/{task_id}/result", response_model=TaskResultResponse)
def get_task_result(
    task_id: UUID,
    service: TaskService = Depends(get_service),
) -> TaskResultResponse:
    task = service.get_task(task_id)
    if task.status is not TaskStatus.COMPLETED or task.result is None:
        raise ApplicationError("RESULT_NOT_READY", "任务结果尚未生成", 409)
    return TaskResultResponse(task_id=task.id, result=task.result)
