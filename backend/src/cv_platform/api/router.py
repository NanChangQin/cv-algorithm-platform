from fastapi import APIRouter

from ..modules.algorithms.api.router import router as algorithms_router
from ..modules.assets.api.router import router as assets_router
from ..modules.comparisons.api.router import router as comparisons_router
from ..modules.deployments.api import router as deployments_router
from ..modules.instances.api.router import router as instances_router
from ..modules.media.api import router as media_router
from ..modules.operations.api import router as operations_router
from ..modules.projects.api import router as projects_router
from ..modules.scheduling.api import router as scheduling_router
from ..modules.security.api import router as security_router
from ..modules.system.api import router as system_router
from ..modules.tasks.api.events import router as task_events_router
from ..modules.tasks.api.router import router as tasks_router
from ..modules.workflows.api import router as workflows_router

api_router = APIRouter()
api_router.include_router(algorithms_router)
api_router.include_router(assets_router)
api_router.include_router(comparisons_router)
api_router.include_router(tasks_router)
api_router.include_router(task_events_router)
api_router.include_router(instances_router)
api_router.include_router(security_router)
api_router.include_router(system_router)
api_router.include_router(media_router)
api_router.include_router(workflows_router)
api_router.include_router(scheduling_router)
api_router.include_router(deployments_router)
api_router.include_router(operations_router)
api_router.include_router(projects_router)
