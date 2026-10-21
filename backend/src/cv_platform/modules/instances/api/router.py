from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from ....core.container import ApplicationContainer
from ....core.request_context import project_id_from
from ....dependencies import get_container
from ..application.service import RuntimeInstanceService
from .schemas import RuntimeInstanceResponse

router = APIRouter(tags=["instances"])


@router.get("/execution-resources")
def execution_resources(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, object]:
    # Task submitters need names/cards, not administrative Manager URLs.
    return {
        "nodes": [
            {"id": node.id, "name": node.name}
            for node in container.algorithm_manager.list_nodes() if node.enabled
        ],
        "gpus": container.algorithm_manager.list_gpus(),
    }


def get_service(
    request: Request,
    container: ApplicationContainer = Depends(get_container),
) -> RuntimeInstanceService:
    return RuntimeInstanceService(
        container.algorithms,
        container.algorithm_manager,
        project_id_from(request),
    )


@router.get("/instances", response_model=list[RuntimeInstanceResponse])
def list_instances(
    service: RuntimeInstanceService = Depends(get_service),
) -> list[RuntimeInstanceResponse]:
    return [RuntimeInstanceResponse.from_entity(item) for item in service.list_instances()]


@router.post("/algorithms/{algorithm_id}/start", response_model=RuntimeInstanceResponse)
def start_algorithm(
    algorithm_id: UUID,
    service: RuntimeInstanceService = Depends(get_service),
) -> RuntimeInstanceResponse:
    return RuntimeInstanceResponse.from_entity(service.start_algorithm(algorithm_id))


@router.post("/instances/{instance_id}/stop", response_model=RuntimeInstanceResponse)
def stop_instance(
    instance_id: str,
    service: RuntimeInstanceService = Depends(get_service),
) -> RuntimeInstanceResponse:
    return RuntimeInstanceResponse.from_entity(service.stop(instance_id))


@router.delete("/instances/{instance_id}", status_code=204)
def remove_instance(
    instance_id: str,
    service: RuntimeInstanceService = Depends(get_service),
) -> Response:
    service.remove(instance_id)
    return Response(status_code=204)


@router.get("/instances/{instance_id}/logs", response_model=list[str])
def instance_logs(
    instance_id: str,
    tail: int = 200,
    service: RuntimeInstanceService = Depends(get_service),
) -> list[str]:
    return service.logs(instance_id, tail)


@router.get("/system/gpus", response_model=list[dict[str, object]])
def list_gpus(
    service: RuntimeInstanceService = Depends(get_service),
) -> list[dict[str, object]]:
    return service.list_gpus()
