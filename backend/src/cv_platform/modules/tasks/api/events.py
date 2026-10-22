import asyncio
import json
from uuid import UUID

from cv_algorithm_sdk import TaskStatus
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ....core.container import ApplicationContainer
from ....core.errors import ApplicationError
from ....core.request_context import project_id_from
from ....dependencies import get_container

router = APIRouter(prefix="/task-events", tags=["tasks"])


@router.get("/{task_id}")
def task_events(
    task_id: UUID,
    request: Request,
    project_id: UUID | None = None,
    container: ApplicationContainer = Depends(get_container),
) -> StreamingResponse:
    resolved_project_id = project_id or project_id_from(request)
    session = getattr(request.state, "session", None)
    if session is None:
        raise ApplicationError("AUTH_REQUIRED", "请先登录", 401)
    container.projects.require_access(resolved_project_id, session.user_id)
    initial = container.tasks.get(task_id)
    if initial is None or initial.project_id != resolved_project_id:
        raise ApplicationError("TASK_NOT_FOUND", "任务不存在", 404)

    async def stream():
        last_updated = None
        while True:
            task = container.tasks.get(task_id)
            if task is None:
                return
            if task.updated_at != last_updated:
                payload = {
                    "task_id": str(task.id),
                    "status": task.status.value,
                    "updated_at": task.updated_at.isoformat(),
                    "error_code": task.error_code,
                    "error_message": task.error_message,
                }
                yield f"event: task\ndata: {json.dumps(payload)}\n\n"
                last_updated = task.updated_at
            if task.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                return
            await asyncio.sleep(0.75)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
