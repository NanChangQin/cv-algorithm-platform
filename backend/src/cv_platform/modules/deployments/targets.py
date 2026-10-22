"""定向部署：把算法版本部署到指定服务器的指定 GPU，并管理启动、停止和重新部署。"""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from typing import Any
from uuid import UUID

from cv_algorithm_sdk import AlgorithmStatus
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from ...core.container import ApplicationContainer
from ...core.errors import ApplicationError
from ...core.request_context import project_id_from
from ...dependencies import get_container
from ..algorithms.domain.repositories import AlgorithmRepository
from ..scheduling.cluster_gateway import ClusterAlgorithmManagerGateway
from .domain import Deployment, DeploymentStatus, initial_steps
from .repository import InMemoryDeploymentRepository, PostgresDeploymentRepository


class DeploymentService:
    def __init__(
        self,
        repository: InMemoryDeploymentRepository | PostgresDeploymentRepository,
        algorithms: AlgorithmRepository,
        cluster: ClusterAlgorithmManagerGateway,
        project_id: UUID,
        actor: str,
    ) -> None:
        self._repository = repository
        self._algorithms = algorithms
        self._cluster = cluster
        self._project_id = project_id
        self._actor = actor

    def list(self) -> list[Deployment]:
        return self._repository.list(self._project_id)

    def get(self, deployment_id: UUID) -> Deployment:
        deployment = self._repository.get(deployment_id)
        if deployment is None or deployment.project_id != self._project_id:
            raise ApplicationError("DEPLOYMENT_NOT_FOUND", "部署不存在", 404)
        return deployment

    def create(
        self, algorithm_version_id: UUID, node_id: str, gpu_device_ids: tuple[str, ...]
    ) -> Deployment:
        algorithm = self._algorithms.get(algorithm_version_id)
        if algorithm is None or algorithm.project_id != self._project_id:
            raise ApplicationError("ALGORITHM_NOT_FOUND", "算法版本不存在", 404)
        if algorithm.status is not AlgorithmStatus.AVAILABLE:
            raise ApplicationError("ALGORITHM_NOT_AVAILABLE", "只能部署可用状态的算法版本", 409)
        device = algorithm.manifest.runtime.device.value
        if device == "cpu" and gpu_device_ids:
            raise ApplicationError("PLACEMENT_INVALID", "该算法只支持 CPU，不能指定 GPU")
        client = self._cluster.node_client(node_id)
        if device != "cpu":
            gpus = client.gpus()
            available = {str(gpu["index"]) for gpu in gpus}
            if not set(gpu_device_ids) <= available:
                raise ApplicationError("GPU_UNAVAILABLE", "所选 GPU 不存在，请刷新后重新选择", 409)
            if not gpu_device_ids and gpus:
                best = max(gpus, key=lambda gpu: int(gpu.get("memory_total_mb", 0))
                           - int(gpu.get("memory_used_mb", 0)))
                gpu_device_ids = (str(best["index"]),)
            if device == "gpu" and not gpu_device_ids:
                raise ApplicationError(
                    "GPU_UNAVAILABLE", f"服务器「{client.node_name}」没有检测到 GPU", 409
                )
        for existing in self._repository.list_for_version(algorithm.id):
            if existing.node_id == node_id and set(existing.gpu_device_ids) == set(gpu_device_ids):
                raise ApplicationError(
                    "DEPLOYMENT_EXISTS", "这个版本已经部署在该服务器的同一张 GPU 上", 409
                )
        deployment = Deployment.queued(
            self._project_id, algorithm.id, node_id, gpu_device_ids, self._actor
        )
        self._repository.add(deployment)
        return deployment

    def stop(self, deployment_id: UUID) -> Deployment:
        deployment = self.get(deployment_id)
        if deployment.status is DeploymentStatus.RUNNING:
            if deployment.instance_id:
                self._cluster.stop(deployment.instance_id)
            self._repository.update(
                deployment.id, {DeploymentStatus.RUNNING}, status=DeploymentStatus.STOPPED
            )
        elif deployment.status is DeploymentStatus.PREPARING:
            self._repository.update(
                deployment.id, {DeploymentStatus.PREPARING}, status=DeploymentStatus.STOPPING
            )
        elif deployment.status is DeploymentStatus.QUEUED:
            self._repository.update(
                deployment.id, {DeploymentStatus.QUEUED}, status=DeploymentStatus.STOPPED
            )
        return self.get(deployment.id)

    def start(self, deployment_id: UUID) -> Deployment:
        """停止或失败后重新执行部署步骤；镜像和权重已就绪时很快完成。"""
        deployment = self.get(deployment_id)
        if not self._repository.update(
            deployment.id, {DeploymentStatus.STOPPED, DeploymentStatus.FAILED},
            status=DeploymentStatus.QUEUED, steps=initial_steps(), error=None,
        ):
            raise ApplicationError("DEPLOYMENT_ACTIVE", "部署已在运行或正在部署", 409)
        return self.get(deployment.id)

    def redeploy(self, deployment_id: UUID) -> Deployment:
        """删除现有容器后重新执行全部部署步骤。"""
        deployment = self.get(deployment_id)
        if deployment.status in {DeploymentStatus.PREPARING, DeploymentStatus.STOPPING}:
            raise ApplicationError("DEPLOYMENT_BUSY", "正在部署，请等当前部署结束", 409)
        self._remove_instance(deployment)
        self._repository.update(
            deployment.id,
            {DeploymentStatus.QUEUED, DeploymentStatus.RUNNING, DeploymentStatus.STOPPED,
             DeploymentStatus.FAILED},
            status=DeploymentStatus.QUEUED, steps=initial_steps(), error=None, instance_id=None,
        )
        return self.get(deployment.id)

    def delete(self, deployment_id: UUID) -> None:
        deployment = self.get(deployment_id)
        if deployment.status in {DeploymentStatus.PREPARING, DeploymentStatus.STOPPING}:
            raise ApplicationError("DEPLOYMENT_BUSY", "正在部署，请等当前部署结束", 409)
        self._remove_instance(deployment)
        self._repository.delete(deployment.id)

    def running_placement(
        self, deployment_id: UUID, algorithm_version_id: UUID
    ) -> tuple[str, tuple[str, ...]]:
        """推理任务指定部署实例时使用的服务器和 GPU。"""
        deployment = self.get(deployment_id)
        if deployment.algorithm_version_id != algorithm_version_id:
            raise ApplicationError("PLACEMENT_INVALID", "所选部署不是这个算法版本的")
        if deployment.status is not DeploymentStatus.RUNNING:
            raise ApplicationError("DEPLOYMENT_NOT_RUNNING", "所选部署实例没有在运行", 409)
        return deployment.node_id, deployment.gpu_device_ids

    def _remove_instance(self, deployment: Deployment) -> None:
        if deployment.instance_id:
            # 实例可能已被手动删除或服务器离线，删不掉不影响重新部署
            with suppress(ApplicationError):
                self._cluster.remove(deployment.instance_id)


router = APIRouter(prefix="/deployments", tags=["deployments"])


def get_service(
    request: Request,
    container: ApplicationContainer = Depends(get_container),
) -> DeploymentService:
    return DeploymentService(
        container.deployments,
        container.algorithms,
        container.algorithm_manager,
        project_id_from(request),
        request.state.session.username,
    )


class CreateDeploymentRequest(BaseModel):
    algorithm_version_id: UUID
    node_id: str = Field(min_length=1, max_length=64)
    gpu_device_ids: list[str] = Field(default_factory=list, max_length=8)


class DeploymentResponse(BaseModel):
    id: UUID
    algorithm_version_id: UUID
    node_id: str
    gpu_device_ids: list[str]
    status: DeploymentStatus
    steps: list[dict[str, Any]]
    instance_id: str | None
    error: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None

    @classmethod
    def of(cls, deployment: Deployment) -> DeploymentResponse:
        return cls(
            id=deployment.id, algorithm_version_id=deployment.algorithm_version_id,
            node_id=deployment.node_id, gpu_device_ids=list(deployment.gpu_device_ids),
            status=deployment.status, steps=list(deployment.steps),
            instance_id=deployment.instance_id, error=deployment.error,
            created_by=deployment.created_by, created_at=deployment.created_at,
            updated_at=deployment.updated_at, started_at=deployment.started_at,
        )


@router.get("", response_model=list[DeploymentResponse])
def list_deployments(service: DeploymentService = Depends(get_service)):
    return [DeploymentResponse.of(item) for item in service.list()]


@router.post("", response_model=DeploymentResponse, status_code=201)
def create_deployment(
    payload: CreateDeploymentRequest, service: DeploymentService = Depends(get_service)
):
    return DeploymentResponse.of(service.create(
        payload.algorithm_version_id, payload.node_id, tuple(payload.gpu_device_ids)
    ))


@router.get("/{deployment_id}", response_model=DeploymentResponse)
def get_deployment(deployment_id: UUID, service: DeploymentService = Depends(get_service)):
    return DeploymentResponse.of(service.get(deployment_id))


@router.post("/{deployment_id}/stop", response_model=DeploymentResponse)
def stop_deployment(deployment_id: UUID, service: DeploymentService = Depends(get_service)):
    return DeploymentResponse.of(service.stop(deployment_id))


@router.post("/{deployment_id}/start", response_model=DeploymentResponse)
def start_deployment(deployment_id: UUID, service: DeploymentService = Depends(get_service)):
    return DeploymentResponse.of(service.start(deployment_id))


@router.post("/{deployment_id}/redeploy", response_model=DeploymentResponse)
def redeploy(deployment_id: UUID, service: DeploymentService = Depends(get_service)):
    return DeploymentResponse.of(service.redeploy(deployment_id))


@router.delete("/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_deployment(deployment_id: UUID, service: DeploymentService = Depends(get_service)):
    service.delete(deployment_id)
    return Response(status_code=204)
