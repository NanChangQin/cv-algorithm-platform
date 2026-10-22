"""后台执行部署：检查服务器 → 准备镜像 → 准备权重 → 启动实例，并持续检查实例是否还在运行。"""

from __future__ import annotations

import logging
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Any
from uuid import UUID

from ...core.errors import ApplicationError
from ..algorithms.domain.entities import AlgorithmVersion
from ..algorithms.domain.repositories import AlgorithmRepository
from ..instances.application.service import RuntimeInstanceService
from ..scheduling.cluster_gateway import ClusterAlgorithmManagerGateway
from ..scheduling.node_client import NodeClient
from .domain import Deployment, DeploymentStatus, initial_steps
from .repository import InMemoryDeploymentRepository, PostgresDeploymentRepository

logger = logging.getLogger(__name__)

# 运行中的部署多久检查一次实例；同时刷新实例的最近使用时间，避免被空闲回收
CHECK_INTERVAL = 60


def model_directory(algorithm: AlgorithmVersion) -> str:
    """节点模型目录下该算法版本使用的子目录。"""
    return algorithm.model_directory or algorithm.manifest.id


class DeploymentOrchestrator:
    def __init__(
        self,
        repository: InMemoryDeploymentRepository | PostgresDeploymentRepository,
        algorithms: AlgorithmRepository,
        cluster: ClusterAlgorithmManagerGateway,
        instances: RuntimeInstanceService,
        staging_root: Path,
    ) -> None:
        self._repository = repository
        self._algorithms = algorithms
        self._cluster = cluster
        self._instances = instances
        self._staging = staging_root
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cv-deploy")
        self._busy: set[UUID] = set()
        self._busy_lock = Lock()
        self._checked_at: dict[UUID, float] = {}

    def tick(self) -> None:
        active = self._repository.list_active()
        due = []
        for deployment in active:
            if deployment.status is DeploymentStatus.QUEUED:
                self._submit(deployment.id, self.deploy)
            elif deployment.status in {DeploymentStatus.PREPARING, DeploymentStatus.STOPPING}:
                # 进程在部署中途重启：停止中的直接结束，部署中的重新排队
                if not self._is_busy(deployment.id) and not self._repository.update(
                    deployment.id, {DeploymentStatus.STOPPING}, status=DeploymentStatus.STOPPED,
                ):
                    self._repository.update(
                        deployment.id, {DeploymentStatus.PREPARING},
                        status=DeploymentStatus.QUEUED, steps=initial_steps(),
                    )
            elif time.monotonic() - self._checked_at.get(deployment.id, 0) >= CHECK_INTERVAL:
                due.append(deployment)
        if due:
            self.check(due)

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=not wait)

    # 部署 -----------------------------------------------------------------

    def deploy(self, deployment_id: UUID) -> None:
        if not self._repository.update(
            deployment_id, {DeploymentStatus.QUEUED},
            status=DeploymentStatus.PREPARING, steps=initial_steps(), error=None,
            instance_id=None,
        ):
            return
        deployment = self._repository.get(deployment_id)
        progress = _Steps(self._repository, deployment)
        try:
            algorithm = self._algorithms.get(deployment.algorithm_version_id)
            if algorithm is None:
                raise ApplicationError("ALGORITHM_NOT_FOUND", "算法版本已被删除")
            progress.start("node")
            target = self._cluster.node_client(deployment.node_id)
            progress.done("node", self._check_node(target, deployment, algorithm))
            progress.start("image")
            progress.done("image", self._ensure_image(target, algorithm, progress))
            progress.start("weights")
            detail, skipped = self._ensure_weights(target, algorithm, progress)
            progress.finish("weights", "skipped" if skipped else "done", detail)
            if self._stopped_while_preparing(deployment_id):
                return
            progress.start("start", "启动容器并等待健康检查通过")
            instance = self._instances.start_algorithm(
                algorithm.id,
                algorithm.project_id,
                node_id=deployment.node_id,
                gpu_device_ids=deployment.gpu_device_ids,
            )
            progress.done("start", f"容器 {instance.container_name}")
            self._checked_at[deployment_id] = time.monotonic()
            if not self._repository.update(
                deployment_id, {DeploymentStatus.PREPARING},
                status=DeploymentStatus.RUNNING, instance_id=instance.id,
                started_at=datetime.now(UTC),
            ):
                # 启动过程中用户点了停止
                self._stop_instance(instance.id)
                self._repository.update(
                    deployment_id, {DeploymentStatus.STOPPING}, status=DeploymentStatus.STOPPED,
                )
        except ApplicationError as exc:
            self._fail(deployment_id, progress, exc.message)
        except Exception:
            logger.exception("deployment failed", extra={"deployment_id": str(deployment_id)})
            self._fail(deployment_id, progress, "部署失败，请查看平台日志")

    def _check_node(
        self, target: NodeClient, deployment: Deployment, algorithm: AlgorithmVersion
    ) -> str:
        device = algorithm.manifest.runtime.device.value
        gpus = target.gpus() if device != "cpu" else []
        if device == "gpu" and not gpus:
            raise ApplicationError("GPU_UNAVAILABLE", f"服务器「{target.node_name}」没有检测到 GPU")
        available = {str(gpu["index"]) for gpu in gpus}
        if not set(deployment.gpu_device_ids) <= available:
            raise ApplicationError("GPU_UNAVAILABLE", "所选 GPU 已不存在，请重新部署到其他 GPU")
        if not deployment.gpu_device_ids:
            return f"「{target.node_name}」可以连接，使用 CPU 运行"
        chosen = [gpu for gpu in gpus if str(gpu["index"]) in deployment.gpu_device_ids]
        free = sum(int(gpu.get("memory_total_mb", 0)) - int(gpu.get("memory_used_mb", 0))
                   for gpu in chosen)
        need = algorithm.manifest.runtime.min_memory_mb
        note = f"，低于算法建议的 {need} MB，可能启动失败" if free < need else ""
        return f"GPU {', '.join(deployment.gpu_device_ids)} 空闲显存 {free} MB{note}"

    def _ensure_image(
        self, target: NodeClient, algorithm: AlgorithmVersion, progress: _Steps
    ) -> str:
        image = algorithm.image
        if target.image_exists(image):
            return f"服务器上已有镜像 {image}"
        for source in self._cluster.node_clients():
            if source.node_id == target.node_id:
                continue
            try:
                info = source.request("GET", "/images/inspect", params={"image": image})
            except ApplicationError:
                continue
            if not info.get("exists"):
                continue
            size = int(info.get("size") or 0) or None
            source.relay(
                target, "/images/export", "/images/import",
                params={"image": image}, size=size,
                progress=_Transfer(progress, "image", f"从「{source.node_name}」传输镜像"),
            )
            return f"已从「{source.node_name}」传输镜像" + (f"（{_mb(size)}）" if size else "")
        if algorithm.package_path and Path(algorithm.package_path).is_dir():
            return self._build_on(target, algorithm, progress)
        raise ApplicationError(
            "IMAGE_UNAVAILABLE",
            f"所有服务器上都没有镜像 {image}，也没有可用于构建的算法包",
        )

    def _build_on(self, target: NodeClient, algorithm: AlgorithmVersion, progress: _Steps) -> str:
        package_path = algorithm.package_path
        if target.node_id != "local":
            progress.update("image", "上传算法包")
            archive = _zip_directory(Path(package_path), self._staging)
            try:
                stored = target.upload(f"/packages/{algorithm.id.hex}", archive)
            finally:
                archive.unlink(missing_ok=True)
            package_path = stored["package_path"]
        progress.update("image", "在服务器上构建镜像，首次构建需要下载依赖，可能要几分钟")
        target.request(
            "POST",
            "/images/build",
            json={
                "package_path": package_path,
                "image": algorithm.image,
                "framework": algorithm.manifest.runtime.framework,
            },
            timeout=3600,
        )
        return "已用算法包在服务器上构建镜像"

    def _ensure_weights(
        self, target: NodeClient, algorithm: AlgorithmVersion, progress: _Steps
    ) -> tuple[str, bool]:
        request = self._instances.runtime_request(algorithm.id, algorithm.project_id)
        if request.model_files:
            # 训练发布的版本：权重保存在主服务器上，按校验值补齐到节点
            uploaded = self._cluster.sync_model_files(
                target.node_id, request,
                progress=_Transfer(progress, "weights", "从主服务器上传权重"),
            )
            directory = request.model_directory
            if not uploaded:
                return f"服务器上已有这个版本的权重（models/{directory}）", False
            return f"已上传 {uploaded} 个文件到 models/{directory}", False
        directory = model_directory(algorithm)
        present = target.request("GET", f"/models/{directory}")["files"]
        for source in self._cluster.node_clients():
            if source.node_id == target.node_id:
                continue
            try:
                files = source.request("GET", f"/models/{directory}")["files"]
            except ApplicationError:
                continue
            if not files:
                continue
            missing = {
                name: item for name, item in files.items()
                if (present.get(name) or {}).get("sha256") != item["sha256"]
            }
            if not missing:
                return f"服务器上已有 {len(present)} 个权重文件（models/{directory}）", False
            total = sum(int(item["size"]) for item in missing.values())
            for name, item in missing.items():
                source.relay(
                    target,
                    f"/models/{directory}/files/{name}",
                    f"/models/{directory}/files/{name}",
                    headers={"X-Content-SHA256": str(item["sha256"])},
                    size=int(item["size"]),
                    progress=_Transfer(progress, "weights", f"从「{source.node_name}」复制 {name}"),
                )
            detail = f"已从「{source.node_name}」复制 {len(missing)} 个权重文件（{_mb(total)}）"
            return detail, False
        if present:
            return f"服务器上已有 {len(present)} 个权重文件（models/{directory}）", False
        return "其他服务器上也没有该算法的外部权重目录，按镜像自带的权重运行", True

    def _stopped_while_preparing(self, deployment_id: UUID) -> bool:
        return self._repository.update(
            deployment_id, {DeploymentStatus.STOPPING}, status=DeploymentStatus.STOPPED,
        )

    def _fail(self, deployment_id: UUID, progress: _Steps, message: str) -> None:
        progress.fail(message)
        if not self._repository.update(
            deployment_id, {DeploymentStatus.PREPARING},
            status=DeploymentStatus.FAILED, error=message,
        ):
            self._repository.update(
                deployment_id, {DeploymentStatus.STOPPING}, status=DeploymentStatus.STOPPED,
            )

    # 运行检查 -------------------------------------------------------------

    def check(self, deployments: list[Deployment]) -> None:
        by_node: dict[str, list[Deployment]] = {}
        for deployment in deployments:
            self._checked_at[deployment.id] = time.monotonic()
            by_node.setdefault(deployment.node_id, []).append(deployment)
        for node_id, items in by_node.items():
            try:
                client = self._cluster.node_client(node_id)
            except ApplicationError as exc:
                # 服务器被删除或停用
                for deployment in items:
                    self._mark_lost(deployment, exc.message)
                continue
            try:
                instances = {item["id"]: item for item in client.request("GET", "/instances")}
            except ApplicationError:
                # 暂时连不上：不改状态，下次再查
                continue
            for deployment in items:
                instance = instances.get(deployment.instance_id or "")
                if instance is not None and instance.get("status") == "healthy":
                    with suppress(ApplicationError):
                        client.request("POST", f"/instances/{instance['id']}/touch")
                    continue
                reason = "实例已被删除" if instance is None else (
                    f"实例状态为 {instance.get('status')}"
                    + (f"：{instance['error']}" if instance.get("error") else "")
                )
                self._mark_lost(deployment, reason)

    def _mark_lost(self, deployment: Deployment, reason: str) -> None:
        self._repository.update(
            deployment.id, {DeploymentStatus.RUNNING},
            status=DeploymentStatus.FAILED, error=f"{reason}，点“启动”重新部署",
        )

    # 工具 -----------------------------------------------------------------

    def _stop_instance(self, instance_id: str) -> None:
        try:
            self._cluster.stop(instance_id)
        except ApplicationError:
            logger.warning("stopping deployed instance failed", extra={"instance": instance_id})

    def _submit(self, deployment_id: UUID, action) -> None:
        with self._busy_lock:
            if deployment_id in self._busy:
                return
            self._busy.add(deployment_id)

        def run() -> None:
            try:
                action(deployment_id)
            finally:
                with self._busy_lock:
                    self._busy.discard(deployment_id)

        self._pool.submit(run)

    def _is_busy(self, deployment_id: UUID) -> bool:
        with self._busy_lock:
            return deployment_id in self._busy


class _Steps:
    """在内存里维护步骤列表，每次变化写回数据库，前端轮询即可看到进度。"""

    def __init__(self, repository: Any, deployment: Deployment) -> None:
        self._repository = repository
        self._id = deployment.id
        self._steps = [dict(step) for step in initial_steps()]
        self._current: str | None = None

    def start(self, key: str, detail: str | None = None) -> None:
        self._current = key
        self._set(key, "running", detail)

    def update(self, key: str, detail: str) -> None:
        self._set(key, "running", detail)

    def done(self, key: str, detail: str) -> None:
        self.finish(key, "done", detail)

    def finish(self, key: str, status: str, detail: str) -> None:
        self._current = None
        self._set(key, status, detail)

    def fail(self, message: str) -> None:
        if self._current:
            self._set(self._current, "failed", message)

    def _set(self, key: str, status: str, detail: str | None) -> None:
        for step in self._steps:
            if step["key"] == key:
                step["status"], step["detail"] = status, detail
        self._repository.update(
            self._id, {DeploymentStatus.PREPARING, DeploymentStatus.STOPPING},
            steps=tuple(dict(step) for step in self._steps),
        )


class _Transfer:
    """传输进度：每秒最多写一次，显示已传输量和速度。"""

    def __init__(self, steps: _Steps, key: str, label: str) -> None:
        self._steps, self._key, self._label = steps, key, label
        self._started = time.monotonic()
        self._written = 0.0

    def __call__(self, sent: int, total: int | None) -> None:
        now = time.monotonic()
        if now - self._written < 1:
            return
        self._written = now
        rate = sent / max(now - self._started, 0.001)
        amount = _mb(sent) + (f" / 约 {_mb(total)}" if total else "")
        self._steps.update(self._key, f"{self._label} {amount}，{_mb(rate)}/s")


def _zip_directory(source: Path, staging: Path) -> Path:
    staging.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=staging, suffix=".zip", delete=False) as target:
        archive = Path(target.name)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file() and not path.is_symlink():
                bundle.write(path, path.relative_to(source).as_posix())
    return archive


def _mb(size: float) -> str:
    return f"{size / 1024**2:.1f} MB"
