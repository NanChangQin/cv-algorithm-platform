from __future__ import annotations

import logging
import zipfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from ...core.errors import ApplicationError
from ..scheduling.cluster_gateway import ClusterAlgorithmManagerGateway
from .datasets import TrainingDatasetStorage
from .domain import (
    ACTIVE_STATUSES,
    TRAINING_MODELS,
    TrainingDataset,
    TrainingRun,
    TrainingStatus,
)
from .repository import InMemoryTrainingRepository, PostgresTrainingRepository

logger = logging.getLogger(__name__)


class TrainingService:
    def __init__(
        self,
        repository: InMemoryTrainingRepository | PostgresTrainingRepository,
        storage: TrainingDatasetStorage,
        cluster: ClusterAlgorithmManagerGateway,
        project_id: UUID,
        actor: str,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._cluster = cluster
        self._project_id = project_id
        self._actor = actor

    # 数据集 ---------------------------------------------------------------

    def import_dataset(self, stream: BinaryIO, filename: str, name: str | None) -> TrainingDataset:
        dataset_id = uuid4()
        imported = self._storage.import_archive(stream, filename, dataset_id)
        dataset = TrainingDataset.create(
            self._project_id,
            (name or Path(filename).stem).strip()[:120] or "未命名数据集",
            imported.class_names,
            imported.stats,
            imported.size_bytes,
            imported.sha256,
            self._actor,
            dataset_id,
        )
        try:
            self._repository.add_dataset(dataset)
        except Exception:
            self._storage.delete(dataset_id)
            raise
        return dataset

    def list_datasets(self) -> list[TrainingDataset]:
        return self._repository.list_datasets(self._project_id)

    def get_dataset(self, dataset_id: UUID) -> TrainingDataset:
        dataset = self._repository.get_dataset(dataset_id)
        if dataset is None or dataset.project_id != self._project_id:
            raise ApplicationError("DATASET_NOT_FOUND", "训练数据集不存在", 404)
        return dataset

    def delete_dataset(self, dataset_id: UUID) -> None:
        dataset = self.get_dataset(dataset_id)
        if self._repository.count_runs_for_dataset(dataset.id):
            raise ApplicationError(
                "DATASET_IN_USE", "已有训练使用这个数据集，请先删除相关训练记录", 409
            )
        self._repository.delete_dataset(dataset.id)
        self._storage.delete(dataset.id)
        for client in self._cluster.node_clients():
            try:
                client.request("DELETE", f"/datasets/{dataset.id}", timeout=10)
            except ApplicationError:
                # 节点离线时数据集副本留在节点上，不影响平台
                continue

    # 训练 -----------------------------------------------------------------

    def create_run(
        self,
        *,
        name: str,
        dataset_id: UUID,
        model: str,
        parameters: dict[str, Any],
        node_id: str,
        gpu_device_ids: tuple[str, ...],
    ) -> TrainingRun:
        dataset = self.get_dataset(dataset_id)
        if model not in {item.key for item in TRAINING_MODELS}:
            raise ApplicationError("MODEL_INVALID", "不支持的预训练模型")
        client = self._cluster.node_client(node_id)
        gpus = client.gpus()
        if not gpus:
            raise ApplicationError(
                "GPU_UNAVAILABLE", f"服务器「{client.node_name}」没有检测到 GPU，无法训练", 409
            )
        available = {str(gpu["index"]) for gpu in gpus}
        if not set(gpu_device_ids) <= available:
            raise ApplicationError("GPU_UNAVAILABLE", "所选 GPU 不存在，请刷新后重新选择", 409)
        if not gpu_device_ids:
            # 未指定 GPU：选空闲显存最多的一张
            best = max(gpus, key=lambda gpu: int(gpu.get("memory_total_mb", 0))
                       - int(gpu.get("memory_used_mb", 0)))
            gpu_device_ids = (str(best["index"]),)
        run = TrainingRun.queued(
            self._project_id,
            name.strip()[:120] or f"{dataset.name} · {model.removesuffix('.pt')}",
            dataset.id,
            model,
            parameters,
            node_id,
            gpu_device_ids,
            self._actor,
        )
        self._repository.add_run(run)
        return run

    def list_runs(self) -> list[TrainingRun]:
        return self._repository.list_runs(self._project_id)

    def get_run(self, run_id: UUID) -> TrainingRun:
        run = self._repository.get_run(run_id)
        if run is None or run.project_id != self._project_id:
            raise ApplicationError("TRAINING_NOT_FOUND", "训练任务不存在", 404)
        return run

    def stop_run(self, run_id: UUID) -> TrainingRun:
        run = self.get_run(run_id)
        now = datetime.now(UTC)
        if run.status is TrainingStatus.QUEUED and self._repository.update(
            run.id, {TrainingStatus.QUEUED},
            status=TrainingStatus.STOPPED, finished_at=now,
        ):
            return self.get_run(run.id)
        if not self._repository.update(
            run.id, {TrainingStatus.PREPARING, TrainingStatus.RUNNING},
            status=TrainingStatus.STOPPING,
        ):
            if run.status is TrainingStatus.STOPPING:
                return run
            raise ApplicationError("TRAINING_NOT_RUNNING", "训练已经结束", 409)
        if run.status is TrainingStatus.RUNNING:
            try:
                self._cluster.node_client(run.node_id).request(
                    "POST", f"/trainings/{run.id}/stop", timeout=90
                )
            except ApplicationError:
                # 节点暂时连不上：后台同步时会继续尝试停止
                logger.warning("training stop request failed", extra={"training_id": str(run.id)})
        return self.get_run(run.id)

    def logs(self, run_id: UUID, tail: int) -> list[str]:
        run = self.get_run(run_id)
        if run.status in ACTIVE_STATUSES and run.started_at is not None:
            try:
                return self._cluster.node_client(run.node_id).request(
                    "GET", f"/trainings/{run.id}/logs", params={"tail": tail}
                )
            except ApplicationError:
                return list(run.logs[-tail:])
        return list(run.logs[-tail:])

    def delete_run(self, run_id: UUID) -> None:
        run = self.get_run(run_id)
        if run.status in ACTIVE_STATUSES:
            raise ApplicationError("TRAINING_ACTIVE", "请先停止训练再删除", 409)
        with suppress(ApplicationError):
            self._cluster.node_client(run.node_id).request(
                "DELETE", f"/trainings/{run.id}", timeout=30
            )
        self._repository.delete_run(run.id)
        self._storage.delete_run(run.id)

    def artifact(self, run_id: UUID, name: str) -> Path:
        run = self.get_run(run_id)
        if name not in run.artifacts:
            raise ApplicationError("ARTIFACT_NOT_FOUND", "训练产物不存在", 404)
        directory = self._storage.run_directory(run.id).resolve()
        path = (directory / name).resolve()
        if directory not in path.parents or not path.is_file():
            raise ApplicationError("ARTIFACT_NOT_FOUND", "训练产物不存在", 404)
        return path

    def archive(self, run_id: UUID) -> Path:
        """全部训练产物打成的 ZIP；训练结束后产物不再变化，生成一次后复用。"""
        run = self.get_run(run_id)
        if not run.artifacts:
            raise ApplicationError("ARTIFACT_NOT_FOUND", "这次训练没有可下载的结果", 404)
        target = self._storage.run_directory(run.id) / ".results.zip"
        if target.is_file():
            return target
        staging = target.with_name(f".results-{uuid4().hex}.zip")
        try:
            with zipfile.ZipFile(staging, "w") as bundle:
                for name in run.artifacts:
                    # 权重和图片本身已压缩
                    compression = (
                        zipfile.ZIP_STORED if Path(name).suffix in {".pt", ".png", ".jpg"}
                        else zipfile.ZIP_DEFLATED
                    )
                    bundle.write(self.artifact(run.id, name), arcname=name,
                                 compress_type=compression)
            staging.replace(target)
        finally:
            staging.unlink(missing_ok=True)
        return target
