from cv_algorithm_sdk.runtime_profile import RuntimeProfile

from ...core.errors import ApplicationError
from .domain import DeploymentStatus


def bound_deployment(
    repository, deployment_id, project_id, algorithm_id, *, allow_draining: bool = False
):
    deployment = repository.get(deployment_id) if repository else None
    if deployment is None or deployment.project_id != project_id:
        raise ApplicationError("DEPLOYMENT_NOT_FOUND", "部署不存在", 404)
    if deployment.algorithm_version_id != algorithm_id:
        raise ApplicationError("PLACEMENT_INVALID", "所选部署不是这个算法版本的")
    allowed = (
        {DeploymentStatus.RUNNING, DeploymentStatus.DRAINING}
        if allow_draining
        else {DeploymentStatus.RUNNING}
    )
    if deployment.status not in allowed or not deployment.instance_id:
        if deployment.status is DeploymentStatus.DRAINING:
            raise ApplicationError(
                "DEPLOYMENT_DRAINING", "所选部署实例正在排空在途请求，不再接收新任务", 409
            )
        raise ApplicationError("DEPLOYMENT_NOT_RUNNING", "所选部署实例没有在运行", 409)
    return deployment


def resolve_parameters(deployment, algorithm, supplied):
    try:
        return RuntimeProfile.model_validate(deployment.runtime_profile or {}).resolve(
            algorithm.manifest, supplied)
    except ValueError as exc:
        raise ApplicationError("PARAMETER_INVALID", str(exc)) from exc
