"""定向部署：把算法版本部署到指定服务器的指定 GPU，并管理启动、停止、重新部署及服务版本路由。"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from cv_algorithm_sdk import AlgorithmStatus
from cv_algorithm_sdk.runtime_profile import RuntimeProfile
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ...core.container import ApplicationContainer
from ...core.errors import ApplicationError
from ...core.request_context import project_id_from
from ...dependencies import get_container
from ..algorithms.domain.repositories import AlgorithmRepository
from ..scheduling.cluster_gateway import ClusterAlgorithmManagerGateway
from .domain import Deployment, DeploymentRoute, DeploymentStatus, initial_steps
from .orchestrator import DEFAULT_DRAIN_TIMEOUT, DeploymentOrchestrator
from .repository import InMemoryDeploymentRepository, PostgresDeploymentRepository


class DeploymentService:
    def __init__(
        self,
        repository: InMemoryDeploymentRepository | PostgresDeploymentRepository,
        algorithms: AlgorithmRepository,
        cluster: ClusterAlgorithmManagerGateway,
        project_id: UUID,
        actor: str,
        orchestrator: DeploymentOrchestrator | None = None,
    ) -> None:
        self._repository = repository
        self._algorithms = algorithms
        self._cluster = cluster
        self._project_id = project_id
        self._actor = actor
        self._orchestrator = orchestrator

    def list(self) -> list[Deployment]:
        return self._repository.list(self._project_id)

    def get(self, deployment_id: UUID) -> Deployment:
        deployment = self._repository.get(deployment_id)
        if deployment is None or deployment.project_id != self._project_id:
            raise ApplicationError("DEPLOYMENT_NOT_FOUND", "部署不存在", 404)
        return deployment

    def create(
        self, algorithm_version_id: UUID, node_id: str, gpu_device_ids: tuple[str, ...],
        runtime_profile: RuntimeProfile | None = None,
    ) -> Deployment:
        algorithm = self._algorithms.get(algorithm_version_id)
        if algorithm is None or algorithm.project_id != self._project_id:
            raise ApplicationError("ALGORITHM_NOT_FOUND", "算法版本不存在", 404)
        if algorithm.status is not AlgorithmStatus.AVAILABLE:
            raise ApplicationError("ALGORITHM_NOT_AVAILABLE", "只能部署可用状态的算法版本", 409)
        if runtime_profile is not None:
            try:
                runtime_profile.resolve(algorithm.manifest, {})
                if not (algorithm.lineage or {}).get("export_job_id") and (
                    runtime_profile.threads != 1 or runtime_profile.warmup_runs != 1
                ):
                    raise ValueError("threads/warmup_runs 仅支持 ONNX CPU 适配器")
            except ValueError as exc:
                raise ApplicationError("RUNTIME_PROFILE_INVALID", str(exc)) from exc
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
            if (existing.node_id == node_id
                and set(existing.gpu_device_ids) == set(gpu_device_ids)
                and existing.runtime_profile == (runtime_profile.model_dump(mode="json")
                                                 if runtime_profile else None)):
                raise ApplicationError(
                    "DEPLOYMENT_EXISTS", "这个版本已经部署在该服务器的同一张 GPU 上", 409
                )
        deployment = Deployment.queued(
            self._project_id, algorithm.id, node_id, gpu_device_ids, self._actor
        )
        deployment = replace(deployment, runtime_profile=(
            runtime_profile.model_dump(mode="json") if runtime_profile else None))
        self._repository.add(deployment)
        return deployment

    def stop(self, deployment_id: UUID) -> Deployment:
        deployment = self.get(deployment_id)
        if deployment.status is DeploymentStatus.RUNNING:
            in_flight = (
                self._orchestrator.in_flight_tasks(deployment.id) if self._orchestrator else 0
            )
            if in_flight > 0:
                self._repository.update(
                    deployment.id, {DeploymentStatus.RUNNING}, status=DeploymentStatus.DRAINING
                )
            else:
                if deployment.instance_id:
                    self._cluster.stop(deployment.instance_id)
                self._repository.update(
                    deployment.id, {DeploymentStatus.RUNNING}, status=DeploymentStatus.STOPPED
                )
        elif deployment.status is DeploymentStatus.DRAINING:
            if deployment.instance_id:
                self._cluster.stop(deployment.instance_id)
            self._repository.update(
                deployment.id, {DeploymentStatus.DRAINING}, status=DeploymentStatus.STOPPED
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
            status=DeploymentStatus.QUEUED, steps=initial_steps(), error=None, readiness=None,
        ):
            raise ApplicationError("DEPLOYMENT_ACTIVE", "部署已在运行或正在部署", 409)
        return self.get(deployment.id)

    def redeploy(self, deployment_id: UUID) -> Deployment:
        """删除现有容器后重新执行全部部署步骤。"""
        deployment = self.get(deployment_id)
        if deployment.status in {
            DeploymentStatus.PREPARING, DeploymentStatus.STOPPING, DeploymentStatus.DRAINING
        }:
            raise ApplicationError("DEPLOYMENT_BUSY", "正在部署或排空请求，请稍后再试", 409)
        self._remove_instance(deployment)
        self._repository.update(
            deployment.id,
            {DeploymentStatus.QUEUED, DeploymentStatus.RUNNING, DeploymentStatus.STOPPED,
             DeploymentStatus.FAILED},
            status=DeploymentStatus.QUEUED, steps=initial_steps(), error=None, readiness=None,
            instance_id=None,
        )
        return self.get(deployment.id)

    def delete(self, deployment_id: UUID) -> None:
        deployment = self.get(deployment_id)
        if deployment.status in {
            DeploymentStatus.PREPARING, DeploymentStatus.STOPPING, DeploymentStatus.DRAINING
        }:
            raise ApplicationError("DEPLOYMENT_BUSY", "正在部署或排空请求，请等结束后再删除", 409)
        active_routes = [
            route.key for route in self._repository.list_routes_for_deployment(deployment.id)
            if route.active_deployment_id == deployment.id
        ]
        if active_routes:
            raise ApplicationError(
                "DEPLOYMENT_IN_USE",
                f"该部署正作为服务入口「{', '.join(active_routes)}」的当前版本，"
                "请先切换或删除服务入口",
                409,
            )
        self._remove_instance(deployment)
        self._repository.delete(deployment.id)

    def running_placement(
        self, deployment_id: UUID, algorithm_version_id: UUID
    ) -> tuple[str, tuple[str, ...]]:
        """推理任务指定部署实例时使用的服务器和 GPU。"""
        deployment = self.get(deployment_id)
        if deployment.algorithm_version_id != algorithm_version_id:
            raise ApplicationError("PLACEMENT_INVALID", "所选部署不是这个算法版本的")
        if deployment.status is DeploymentStatus.DRAINING:
            raise ApplicationError(
                "DEPLOYMENT_DRAINING", "所选部署实例正在排空在途请求，不再接收新任务", 409
            )
        if deployment.status is not DeploymentStatus.RUNNING:
            raise ApplicationError("DEPLOYMENT_NOT_RUNNING", "所选部署实例没有在运行", 409)
        return deployment.node_id, deployment.gpu_device_ids

    def in_flight_count(self, deployment_id: UUID) -> int:
        return self._orchestrator.in_flight_tasks(deployment_id) if self._orchestrator else 0

    # 服务入口与版本路由 ---------------------------------------------------

    def list_routes(self) -> list[DeploymentRoute]:
        return self._repository.list_routes(self._project_id)

    def get_route(self, key: str) -> DeploymentRoute:
        route = self._repository.get_route(self._project_id, key)
        if route is None:
            raise ApplicationError("SERVICE_ROUTE_NOT_FOUND", f"服务入口「{key}」不存在", 404)
        return route

    def save_route(
        self,
        key: str,
        target_deployment_id: UUID,
        *,
        name: str | None = None,
        drain_timeout_seconds: int = DEFAULT_DRAIN_TIMEOUT,
    ) -> DeploymentRoute:
        target = self.get(target_deployment_id)
        existing = self._repository.get_route(self._project_id, key)
        resolved_name = (name or (existing.name if existing else key)).strip() or key
        if existing is None:
            if target.status is not DeploymentStatus.RUNNING or not target.instance_id:
                raise ApplicationError(
                    "DEPLOYMENT_NOT_RUNNING",
                    "首次绑定服务入口的部署实例必须处于运行且就绪状态",
                    409,
                )
            route = DeploymentRoute.create(
                self._project_id,
                key,
                resolved_name,
                target.id,
                self._actor,
                drain_timeout_seconds=drain_timeout_seconds,
            )
            self._repository.add_route(route)
            return route

        if target.id == existing.active_deployment_id:
            updated = self._repository.update_route(
                self._project_id,
                key,
                name=resolved_name,
                drain_timeout_seconds=drain_timeout_seconds,
                candidate_deployment_id=None,
                last_error=None,
                updated_by=self._actor,
            )
            assert updated is not None
            return updated

        if target.status is DeploymentStatus.RUNNING and target.instance_id:
            updated = self._repository.update_route(
                self._project_id,
                key,
                name=resolved_name,
                active_deployment_id=target.id,
                previous_deployment_id=existing.active_deployment_id,
                candidate_deployment_id=None,
                drain_timeout_seconds=drain_timeout_seconds,
                last_error=None,
                updated_by=self._actor,
            )
            if self._orchestrator is not None:
                self._orchestrator.retire_deployment(
                    existing.active_deployment_id,
                    drain_timeout_seconds=drain_timeout_seconds,
                )
            assert updated is not None
            return updated

        if target.status is DeploymentStatus.DRAINING:
            # 正在排空的旧部署重新切回线上
            self._repository.update(
                target.id, {DeploymentStatus.DRAINING}, status=DeploymentStatus.RUNNING,
            )
            updated = self._repository.update_route(
                self._project_id,
                key,
                name=resolved_name,
                active_deployment_id=target.id,
                previous_deployment_id=existing.active_deployment_id,
                candidate_deployment_id=None,
                drain_timeout_seconds=drain_timeout_seconds,
                last_error=None,
                updated_by=self._actor,
            )
            if self._orchestrator is not None:
                self._orchestrator.retire_deployment(
                    existing.active_deployment_id,
                    drain_timeout_seconds=drain_timeout_seconds,
                )
            assert updated is not None
            return updated

        # 目标部署尚未就绪（queued / preparing / stopped / failed）：
        # 若为 stopped/failed 则自动触发启动，保持当前线上版本，待目标就绪验证后自动切流。
        if target.status in {DeploymentStatus.STOPPED, DeploymentStatus.FAILED}:
            self._repository.update(
                target.id,
                {DeploymentStatus.STOPPED, DeploymentStatus.FAILED},
                status=DeploymentStatus.QUEUED,
                steps=initial_steps(),
                error=None,
                readiness=None,
            )
        updated = self._repository.update_route(
            self._project_id,
            key,
            name=resolved_name,
            candidate_deployment_id=target.id,
            drain_timeout_seconds=drain_timeout_seconds,
            last_error=None,
            updated_by=self._actor,
        )
        assert updated is not None
        return updated

    def rollback_route(self, key: str) -> DeploymentRoute:
        route = self.get_route(key)
        if route.previous_deployment_id is None:
            raise ApplicationError(
                "ROLLBACK_UNAVAILABLE", "该服务入口没有可回滚的上一版本部署", 409
            )
        return self.save_route(
            key,
            route.previous_deployment_id,
            name=route.name,
            drain_timeout_seconds=route.drain_timeout_seconds,
        )

    def delete_route(self, key: str) -> None:
        if not self._repository.delete_route(self._project_id, key):
            raise ApplicationError("SERVICE_ROUTE_NOT_FOUND", f"服务入口「{key}」不存在", 404)

    def resolve_route_deployment(self, key: str) -> Deployment:
        route = self.get_route(key)
        deployment = self._repository.get(route.active_deployment_id)
        if (
            deployment is not None
            and deployment.project_id == self._project_id
            and deployment.status is DeploymentStatus.RUNNING
            and deployment.instance_id
        ):
            return deployment
        # 当前线上部署已不可用时，若上一版本仍在运行或排空中则立即回退使用
        if route.previous_deployment_id is not None:
            previous = self._repository.get(route.previous_deployment_id)
            if (
                previous is not None
                and previous.project_id == self._project_id
                and previous.status in {DeploymentStatus.RUNNING, DeploymentStatus.DRAINING}
                and previous.instance_id
            ):
                if previous.status is DeploymentStatus.DRAINING:
                    self._repository.update(
                        previous.id, {DeploymentStatus.DRAINING}, status=DeploymentStatus.RUNNING,
                    )
                self._repository.update_route(
                    self._project_id,
                    key,
                    active_deployment_id=previous.id,
                    previous_deployment_id=route.active_deployment_id,
                    candidate_deployment_id=None,
                    last_error="主部署不可用，已自动回退至上一运行版本",
                    updated_by="system",
                )
                return self.get(previous.id)
        raise ApplicationError(
            "SERVICE_ROUTE_UNAVAILABLE",
            f"服务入口「{key}」关联的部署实例当前不在运行",
            409,
        )

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
        orchestrator=container.deployment_orchestrator,
    )


class CreateDeploymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_profile: RuntimeProfile | None = None
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
    runtime_profile: dict[str, Any] | None = None
    readiness: dict[str, Any] | None = None
    in_flight_tasks: int = 0

    @classmethod
    def of(cls, deployment: Deployment, *, in_flight_tasks: int = 0) -> DeploymentResponse:
        return cls(
            id=deployment.id, algorithm_version_id=deployment.algorithm_version_id,
            node_id=deployment.node_id, gpu_device_ids=list(deployment.gpu_device_ids),
            status=deployment.status, steps=list(deployment.steps),
            instance_id=deployment.instance_id, error=deployment.error,
            created_by=deployment.created_by, created_at=deployment.created_at,
            updated_at=deployment.updated_at, started_at=deployment.started_at,
            runtime_profile=deployment.runtime_profile, readiness=deployment.readiness,
            in_flight_tasks=in_flight_tasks,
        )


class UpsertDeploymentRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    target_deployment_id: UUID
    drain_timeout_seconds: int = Field(default=DEFAULT_DRAIN_TIMEOUT, ge=5, le=3600)


class DeploymentRouteResponse(BaseModel):
    id: UUID
    key: str
    name: str
    active_deployment_id: UUID
    active_algorithm_version_id: UUID | None = None
    previous_deployment_id: UUID | None = None
    previous_algorithm_version_id: UUID | None = None
    candidate_deployment_id: UUID | None = None
    candidate_algorithm_version_id: UUID | None = None
    rollout_status: str
    drain_timeout_seconds: int
    last_error: str | None = None
    updated_by: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, route: DeploymentRoute, service: DeploymentService) -> DeploymentRouteResponse:
        active = service._repository.get(route.active_deployment_id)
        previous = (
            service._repository.get(route.previous_deployment_id)
            if route.previous_deployment_id else None
        )
        candidate = (
            service._repository.get(route.candidate_deployment_id)
            if route.candidate_deployment_id else None
        )
        if candidate is not None:
            rollout_status = "switching"
        elif previous is not None and previous.status is DeploymentStatus.DRAINING:
            rollout_status = "draining"
        elif active is None or active.status is not DeploymentStatus.RUNNING:
            rollout_status = "degraded"
        else:
            rollout_status = "active"
        return cls(
            id=route.id,
            key=route.key,
            name=route.name,
            active_deployment_id=route.active_deployment_id,
            active_algorithm_version_id=active.algorithm_version_id if active else None,
            previous_deployment_id=route.previous_deployment_id,
            previous_algorithm_version_id=previous.algorithm_version_id if previous else None,
            candidate_deployment_id=route.candidate_deployment_id,
            candidate_algorithm_version_id=candidate.algorithm_version_id if candidate else None,
            rollout_status=rollout_status,
            drain_timeout_seconds=route.drain_timeout_seconds,
            last_error=route.last_error,
            updated_by=route.updated_by,
            created_at=route.created_at,
            updated_at=route.updated_at,
        )


@router.get("/routes", response_model=list[DeploymentRouteResponse])
def list_deployment_routes(service: DeploymentService = Depends(get_service)):
    return [DeploymentRouteResponse.of(route, service) for route in service.list_routes()]


@router.get("/routes/{key}", response_model=DeploymentRouteResponse)
def get_deployment_route(key: str, service: DeploymentService = Depends(get_service)):
    return DeploymentRouteResponse.of(service.get_route(key), service)


@router.put("/routes/{key}", response_model=DeploymentRouteResponse)
def save_deployment_route(
    key: str,
    payload: UpsertDeploymentRouteRequest,
    service: DeploymentService = Depends(get_service),
):
    if not key or len(key) < 2 or len(key) > 64 or not all(
        ch.isascii() and (ch.islower() or ch.isdigit() or ch == "-") for ch in key
    ) or key[0] == "-" or key[-1] == "-":
        raise ApplicationError(
            "SERVICE_ROUTE_KEY_INVALID", "服务入口标识需为 2～64 位小写字母、数字或连字符"
        )
    route = service.save_route(
        key,
        payload.target_deployment_id,
        name=payload.name,
        drain_timeout_seconds=payload.drain_timeout_seconds,
    )
    return DeploymentRouteResponse.of(route, service)


@router.post("/routes/{key}/rollback", response_model=DeploymentRouteResponse)
def rollback_deployment_route(key: str, service: DeploymentService = Depends(get_service)):
    return DeploymentRouteResponse.of(service.rollback_route(key), service)


@router.delete("/routes/{key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_deployment_route(key: str, service: DeploymentService = Depends(get_service)):
    service.delete_route(key)
    return Response(status_code=204)


@router.get("", response_model=list[DeploymentResponse])
def list_deployments(service: DeploymentService = Depends(get_service)):
    return [
        DeploymentResponse.of(item, in_flight_tasks=service.in_flight_count(item.id))
        for item in service.list()
    ]


@router.post("", response_model=DeploymentResponse, status_code=201)
def create_deployment(
    payload: CreateDeploymentRequest, service: DeploymentService = Depends(get_service)
):
    item = service.create(
        payload.algorithm_version_id, payload.node_id, tuple(payload.gpu_device_ids),
        payload.runtime_profile
    )
    return DeploymentResponse.of(item, in_flight_tasks=0)


@router.get("/{deployment_id}", response_model=DeploymentResponse)
def get_deployment(deployment_id: UUID, service: DeploymentService = Depends(get_service)):
    item = service.get(deployment_id)
    return DeploymentResponse.of(item, in_flight_tasks=service.in_flight_count(item.id))


@router.post("/{deployment_id}/stop", response_model=DeploymentResponse)
def stop_deployment(deployment_id: UUID, service: DeploymentService = Depends(get_service)):
    item = service.stop(deployment_id)
    return DeploymentResponse.of(item, in_flight_tasks=service.in_flight_count(item.id))


@router.post("/{deployment_id}/start", response_model=DeploymentResponse)
def start_deployment(deployment_id: UUID, service: DeploymentService = Depends(get_service)):
    item = service.start(deployment_id)
    return DeploymentResponse.of(item, in_flight_tasks=service.in_flight_count(item.id))


@router.post("/{deployment_id}/redeploy", response_model=DeploymentResponse)
def redeploy(deployment_id: UUID, service: DeploymentService = Depends(get_service)):
    item = service.redeploy(deployment_id)
    return DeploymentResponse.of(item, in_flight_tasks=service.in_flight_count(item.id))


@router.delete("/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_deployment(deployment_id: UUID, service: DeploymentService = Depends(get_service)):
    service.delete(deployment_id)
    return Response(status_code=204)
