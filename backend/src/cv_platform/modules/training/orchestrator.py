"""后台推进训练任务：准备（检查节点、上传数据集、启动容器）、同步进度、收回训练产物。

只在一个进程里运行：PostgreSQL 模式下用 advisory lock 选出唯一的协调者。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, suppress
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import UUID

from ...core.database import Database
from ...core.errors import ApplicationError
from ..scheduling.cluster_gateway import ClusterAlgorithmManagerGateway
from ..scheduling.node_client import NodeClient
from .datasets import TrainingDatasetStorage
from .domain import TrainingRun, TrainingStatus
from .repository import InMemoryTrainingRepository, PostgresTrainingRepository

logger = logging.getLogger(__name__)

NODE_FINISHED = {"completed": TrainingStatus.COMPLETED, "stopped": TrainingStatus.STOPPED}
LOG_TAIL = 500


class LeaderLock:
    """会话级 advisory lock：持有它的进程负责后台协调，进程退出后自动让出。"""

    def __init__(self, database: Database | None, name: str) -> None:
        self._database = database
        self._name = name
        self._stack: ExitStack | None = None
        self._connection: Any = None

    def held(self) -> bool:
        if self._database is None:
            return True
        if self._connection is not None:
            try:
                self._connection.execute("SELECT 1")
                return True
            except Exception:
                self.release()
        stack = ExitStack()
        try:
            connection = stack.enter_context(self._database.dedicated())
            row = connection.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s)) AS locked", (self._name,)
            ).fetchone()
        except Exception:
            stack.close()
            return False
        if not row["locked"]:
            stack.close()
            return False
        self._stack, self._connection = stack, connection
        return True

    def release(self) -> None:
        if self._stack is not None:
            self._stack.close()
        self._stack, self._connection = None, None


class TrainingOrchestrator:
    def __init__(
        self,
        repository: InMemoryTrainingRepository | PostgresTrainingRepository,
        storage: TrainingDatasetStorage,
        cluster: ClusterAlgorithmManagerGateway,
        training_image: str,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._cluster = cluster
        self._image = training_image
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cv-training")
        self._busy: set[UUID] = set()
        self._busy_lock = Lock()

    def tick(self) -> None:
        for run in self._repository.list_active():
            if run.status is TrainingStatus.QUEUED:
                self._submit(run.id, self.prepare)
            elif run.status is TrainingStatus.PREPARING:
                # 进程在准备中途重启：重新排队，准备步骤可以重复执行
                if not self._is_busy(run.id):
                    self._repository.update(
                        run.id, {TrainingStatus.PREPARING}, status=TrainingStatus.QUEUED,
                    )
            elif run.status is TrainingStatus.STOPPING and run.started_at is None:
                if not self._is_busy(run.id):
                    self._finish_unstarted(run.id)
            else:
                self._submit(run.id, self.sync)

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=not wait)

    # 准备 -----------------------------------------------------------------

    def prepare(self, run_id: UUID) -> None:
        if not self._repository.update(
            run_id, {TrainingStatus.QUEUED},
            status=TrainingStatus.PREPARING, stage="检查训练服务器", error=None,
        ):
            return
        run = self._repository.get_run(run_id)
        client: NodeClient | None = None
        start_sent = False
        try:
            client = self._cluster.node_client(run.node_id)
            self._check_gpus(client, run)
            self._stage(run_id, "检查训练镜像")
            if not client.image_exists(self._image):
                raise ApplicationError(
                    "TRAINING_IMAGE_MISSING",
                    f"服务器「{client.node_name}」上没有训练镜像 {self._image}，"
                    "请先在该服务器构建训练镜像（见 docs/TRAINING.md）",
                    409,
                )
            self._send_dataset(client, run)
            if self._finish_unstarted(run_id):
                return
            self._stage(run_id, "启动训练容器")
            start_sent = True
            client.request(
                "POST",
                "/trainings",
                json={
                    "training_id": str(run.id),
                    "dataset_id": str(run.dataset_id),
                    "image": self._image,
                    "parameters": {"model": run.model, **run.parameters},
                    "gpu_device_ids": list(run.gpu_device_ids),
                },
                timeout=120,
            )
            # 准备期间用户点了停止：状态已是 stopping，交给同步流程去停容器
            self._repository.update(
                run_id, {TrainingStatus.PREPARING, TrainingStatus.STOPPING},
                stage=None, started_at=datetime.now(UTC),
            )
            self._repository.update(
                run_id, {TrainingStatus.PREPARING}, status=TrainingStatus.RUNNING,
            )
        except ApplicationError as exc:
            self._fail(run_id, exc.message)
            self._discard_started(client, run_id, start_sent)
        except Exception:
            logger.exception("training preparation failed", extra={"training_id": str(run_id)})
            self._fail(run_id, "训练准备失败，请查看平台日志")
            self._discard_started(client, run_id, start_sent)

    @staticmethod
    def _discard_started(client: NodeClient | None, run_id: UUID, start_sent: bool) -> None:
        """启动请求超时等情况下容器可能已在节点上运行，标记失败后不能让它继续占用 GPU。"""
        if client is not None and start_sent:
            with suppress(ApplicationError):
                client.request("DELETE", f"/trainings/{run_id}", timeout=60)

    def _check_gpus(self, client: NodeClient, run: TrainingRun) -> None:
        available = {str(gpu["index"]) for gpu in client.gpus()}
        if not available:
            raise ApplicationError("GPU_UNAVAILABLE", f"服务器「{client.node_name}」没有检测到 GPU")
        if not set(run.gpu_device_ids) <= available:
            raise ApplicationError("GPU_UNAVAILABLE", "所选 GPU 已不存在，请重新选择")

    def _send_dataset(self, client: NodeClient, run: TrainingRun) -> None:
        dataset = self._repository.get_dataset(run.dataset_id)
        if dataset is None:
            raise ApplicationError("DATASET_NOT_FOUND", "训练数据集已被删除")
        current = client.request("GET", f"/datasets/{dataset.id}", allow_missing=True)
        if current and current.get("sha256") == dataset.sha256:
            return
        last = {"at": 0.0}

        def report(sent: int, total: int | None) -> None:
            now = time.monotonic()
            if now - last["at"] >= 1 and total:
                last["at"] = now
                self._stage(run.id, f"上传数据集 {sent * 100 // total}%")

        self._stage(run.id, "上传数据集")
        client.upload(
            f"/datasets/{dataset.id}",
            self._storage.path(dataset.id),
            headers={"X-Content-SHA256": dataset.sha256},
            progress=report,
        )

    def _finish_unstarted(self, run_id: UUID) -> bool:
        """训练容器启动前就被停止的任务直接结束。"""
        return self._repository.update(
            run_id, {TrainingStatus.STOPPING},
            status=TrainingStatus.STOPPED, stage=None, finished_at=datetime.now(UTC),
        )

    # 同步 -----------------------------------------------------------------

    def sync(self, run_id: UUID) -> None:
        run = self._repository.get_run(run_id)
        if run is None or run.status not in {TrainingStatus.RUNNING, TrainingStatus.STOPPING}:
            return
        try:
            client = self._cluster.node_client(run.node_id)
            state = client.request("GET", f"/trainings/{run.id}")
        except ApplicationError as exc:
            if exc.code == "TRAINING_NOT_FOUND":
                self._fail(run.id, "训练服务器上找不到该训练，可能已被清理")
            elif run.stage != (stage := f"{exc.message}，稍后重试"):
                self._stage(run.id, stage)
            return
        node_state = state.get("state")
        if run.status is TrainingStatus.STOPPING and node_state in {"running", "starting"}:
            try:
                client.request("POST", f"/trainings/{run.id}/stop", timeout=90)
            except ApplicationError as exc:
                self._stage(run.id, f"停止训练失败：{exc.message}，稍后重试")
            return
        metrics = tuple(state.get("metrics") or ())
        progress = state.get("progress")
        if metrics != run.metrics or progress != run.progress or run.stage:
            self._repository.update(
                run.id, {TrainingStatus.RUNNING, TrainingStatus.STOPPING},
                progress=progress, metrics=metrics, stage=None,
            )
        if node_state in {"completed", "stopped", "failed"}:
            self._collect(client, run, state)

    def _collect(self, client: NodeClient, run: TrainingRun, state: dict[str, Any]) -> None:
        directory = self._storage.run_directory(run.id)
        artifacts: dict[str, int] = {}
        try:
            for name in state.get("files") or {}:
                artifacts[name] = client.download(
                    f"/trainings/{run.id}/files/{name}", directory / name
                )
            logs = client.request(
                "GET", f"/trainings/{run.id}/logs", params={"tail": LOG_TAIL}, allow_missing=True
            ) or []
        except ApplicationError as exc:
            self._stage(run.id, f"收回训练结果失败：{exc.message}，稍后重试")
            return
        node_state = state["state"]
        status = NODE_FINISHED.get(node_state) or (
            TrainingStatus.STOPPED if run.status is TrainingStatus.STOPPING
            else TrainingStatus.FAILED
        )
        finished = self._repository.update(
            run.id, {TrainingStatus.RUNNING, TrainingStatus.STOPPING},
            status=status,
            stage=None,
            progress=state.get("progress"),
            metrics=tuple(state.get("metrics") or ()),
            artifacts=artifacts,
            logs=tuple(logs[-LOG_TAIL:]),
            error=state.get("error") if status is TrainingStatus.FAILED else None,
            finished_at=datetime.now(UTC),
        )
        if finished:
            try:
                # 产物已收回主服务器，释放训练节点上的容器和文件
                client.request("DELETE", f"/trainings/{run.id}")
            except ApplicationError:
                logger.warning(
                    "training cleanup on node failed", extra={"training_id": str(run.id)}
                )

    # 工具 -----------------------------------------------------------------

    def _submit(self, run_id: UUID, action) -> None:
        with self._busy_lock:
            if run_id in self._busy:
                return
            self._busy.add(run_id)

        def run() -> None:
            try:
                action(run_id)
            except Exception:
                logger.exception("training coordination failed", extra={"training_id": str(run_id)})
            finally:
                with self._busy_lock:
                    self._busy.discard(run_id)

        self._pool.submit(run)

    def _is_busy(self, run_id: UUID) -> bool:
        with self._busy_lock:
            return run_id in self._busy

    def _stage(self, run_id: UUID, stage: str) -> None:
        self._repository.update(
            run_id,
            {TrainingStatus.PREPARING, TrainingStatus.RUNNING, TrainingStatus.STOPPING},
            stage=stage[:200],
        )

    def _fail(self, run_id: UUID, message: str) -> None:
        now = datetime.now(UTC)
        if self._repository.update(
            run_id, {TrainingStatus.PREPARING, TrainingStatus.RUNNING},
            status=TrainingStatus.FAILED, stage=None, error=message, finished_at=now,
        ):
            return
        # 用户已经要求停止的任务出错时按已停止处理
        self._repository.update(
            run_id, {TrainingStatus.STOPPING},
            status=TrainingStatus.STOPPED, stage=None, finished_at=now,
        )
