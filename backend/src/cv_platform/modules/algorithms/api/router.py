from uuid import UUID

from cv_algorithm_sdk import AlgorithmStatus, ResultType
from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from ....core.container import ApplicationContainer
from ....core.request_context import project_id_from
from ....dependencies import get_container
from ..application.service import AlgorithmService
from ..template_package import build_algorithm_template, template_filename
from .schemas import AlgorithmResponse, BuildJobResponse

router = APIRouter(tags=["algorithms"])


def get_service(
    request: Request,
    container: ApplicationContainer = Depends(get_container),
) -> AlgorithmService:
    return AlgorithmService(
        container.algorithms,
        container.build_jobs,
        container.package_storage,
        container.build_queue,
        container.tasks,
        container.build_gateway,
        container.algorithm_manager,
        project_id_from(request),
        request.state.session.username,
    )


def response_context(
    container: ApplicationContainer,
    project_id: UUID,
) -> dict[UUID, tuple[str, object]]:
    try:
        instances = container.algorithm_manager.list_instances()
    except Exception:
        instances = []
    statuses: dict[UUID, str] = {}
    for instance in instances:
        try:
            version_id = UUID(instance.algorithm_version_id)
        except ValueError:
            continue
        current = statuses.get(version_id)
        if current != "healthy":
            statuses[version_id] = instance.status
    last_calls = container.tasks.last_created_by_algorithm(project_id)
    return {
        algorithm.id: (
            statuses.get(algorithm.id, "stopped"),
            last_calls.get(algorithm.id),
        )
        for algorithm in container.algorithms.list()
        if algorithm.project_id == project_id
    }


@router.get("/algorithms", response_model=list[AlgorithmResponse])
def list_algorithms(
    request: Request,
    query: str | None = None,
    task_type: ResultType | None = None,
    algorithm_status: AlgorithmStatus | None = None,
    service: AlgorithmService = Depends(get_service),
    container: ApplicationContainer = Depends(get_container),
) -> list[AlgorithmResponse]:
    normalized_query = query.lower().strip() if query else None
    items = [
        item
        for item in service.list_algorithms()
        if (
            normalized_query is None
            or normalized_query in item.manifest.name.lower()
            or normalized_query in item.manifest.id
        )
        and (task_type is None or item.manifest.task_type is task_type)
        and (algorithm_status is None or item.status is algorithm_status)
    ]
    contexts = response_context(container, project_id_from(request))
    return [
        AlgorithmResponse.from_entity(
            item,
            container_status=contexts[item.id][0],
            last_called_at=contexts[item.id][1],
        )
        for item in items
    ]


@router.post("/algorithms/import", response_model=AlgorithmResponse, status_code=201)
def import_algorithm(
    package: UploadFile = File(...),
    service: AlgorithmService = Depends(get_service),
) -> AlgorithmResponse:
    algorithm = service.import_package(package.file, package.filename or "")
    return AlgorithmResponse.from_entity(algorithm)


@router.get("/algorithms/template", response_class=StreamingResponse)
def download_algorithm_template(
    task_type: ResultType = ResultType.OBJECT_DETECTION,
) -> StreamingResponse:
    return StreamingResponse(
        build_algorithm_template(task_type),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{template_filename(task_type)}"',
        },
    )


@router.get("/algorithms/{algorithm_id}", response_model=AlgorithmResponse)
def get_algorithm(
    algorithm_id: UUID,
    service: AlgorithmService = Depends(get_service),
) -> AlgorithmResponse:
    return AlgorithmResponse.from_entity(service.get_algorithm(algorithm_id))


@router.get("/algorithm-families/{algorithm_key}/versions", response_model=list[AlgorithmResponse])
def list_algorithm_versions(
    algorithm_key: str,
    service: AlgorithmService = Depends(get_service),
) -> list[AlgorithmResponse]:
    return [
        AlgorithmResponse.from_entity(item)
        for item in service.list_algorithms()
        if item.manifest.id == algorithm_key
    ]


@router.post("/algorithm-versions/{algorithm_id}/build", response_model=BuildJobResponse)
def build_algorithm(
    algorithm_id: UUID,
    service: AlgorithmService = Depends(get_service),
) -> BuildJobResponse:
    return BuildJobResponse.from_entity(service.build(algorithm_id))


@router.post("/algorithm-versions/{algorithm_id}/enable", response_model=AlgorithmResponse)
def enable_algorithm(
    algorithm_id: UUID,
    service: AlgorithmService = Depends(get_service),
) -> AlgorithmResponse:
    return AlgorithmResponse.from_entity(service.set_enabled(algorithm_id, True))


@router.post("/algorithm-versions/{algorithm_id}/disable", response_model=AlgorithmResponse)
def disable_algorithm(
    algorithm_id: UUID,
    service: AlgorithmService = Depends(get_service),
) -> AlgorithmResponse:
    return AlgorithmResponse.from_entity(service.set_enabled(algorithm_id, False))


@router.post("/algorithm-versions/{algorithm_id}/rollback", response_model=list[AlgorithmResponse])
def rollback_algorithm(
    algorithm_id: UUID,
    service: AlgorithmService = Depends(get_service),
) -> list[AlgorithmResponse]:
    return [AlgorithmResponse.from_entity(item) for item in service.rollback(algorithm_id)]


@router.delete("/algorithm-versions/{algorithm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_algorithm(
    algorithm_id: UUID,
    remove_image: bool = False,
    service: AlgorithmService = Depends(get_service),
    container: ApplicationContainer = Depends(get_container),
) -> Response:
    service.delete(algorithm_id, remove_image=remove_image)
    # 实例已随版本一起删除，部署记录也不再有意义
    for deployment in container.deployments.list_for_version(algorithm_id):
        container.deployments.delete(deployment.id)
    container.model_store.delete(algorithm_id)
    return Response(status_code=204)


@router.get("/build-jobs/{job_id}", response_model=BuildJobResponse)
def get_build_job(
    job_id: UUID,
    service: AlgorithmService = Depends(get_service),
) -> BuildJobResponse:
    return BuildJobResponse.from_entity(service.get_build_job(job_id))


@router.get("/build-jobs/{job_id}/logs", response_model=list[str])
def get_build_logs(
    job_id: UUID,
    service: AlgorithmService = Depends(get_service),
) -> list[str]:
    return list(service.get_build_job(job_id).logs)
