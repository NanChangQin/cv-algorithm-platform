from fastapi import APIRouter, Depends, Request

from ...dependencies import get_container
from ...core.request_context import project_id_from


router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def system_health(request: Request, container=Depends(get_container)) -> dict[str, object]:
    project_id = project_id_from(request)
    try:
        all_instances = container.algorithm_manager.list_instances()
        algorithm_ids = {
            str(item.id)
            for item in container.algorithms.list()
            if item.project_id == project_id
        }
        instances = [item for item in all_instances if item.algorithm_version_id in algorithm_ids]
        manager = "ok"
    except Exception:
        instances = []
        manager = "unavailable"
    database = (
        "ok"
        if container.database is not None and container.database.is_healthy()
        else "unavailable" if container.database is not None else "not_configured"
    )
    healthy = manager == "ok" and database != "unavailable"
    return {
        "status": "ok" if healthy else "degraded",
        "services": {
            "platform_api": "ok",
            "algorithm_manager": manager,
            "database": database,
            "task_queue": "postgres_worker" if container.database is not None else "in_process",
            "file_storage": "local",
        },
        "counts": {
            "algorithms": len([item for item in container.algorithms.list() if item.project_id == project_id]),
            "assets": len([item for item in container.assets.list() if item.project_id == project_id]),
            "tasks": len([item for item in container.tasks.list() if item.project_id == project_id]),
            "instances": len(instances),
        },
    }
