from __future__ import annotations

import json
import logging
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from cv_algorithm_sdk.exports import ExportVerification, verification_parameters
from cv_algorithm_sdk.runtime_contract import (
    parameter_warnings,
    validate_placement,
)
from cv_algorithm_sdk.training_contract import (
    FASTER_MODEL,
    FASTER_RESUME_OVERRIDES,
    contract_for,
    is_faster,
    normalize_for,
    protocol_for,
)
from cv_algorithm_sdk.training_sources import RESUME_OVERRIDES, TrainingSource

from ...core.errors import ApplicationError
from ..scheduling.cluster_gateway import ClusterAlgorithmManagerGateway
from .datasets import SplitConfig, TrainingDatasetStorage
from .domain import (
    ACTIVE_STATUSES,
    TRAINING_MODELS,
    TrainingDataset,
    TrainingRun,
    TrainingStatus,
)
from .repository import InMemoryTrainingRepository, PostgresTrainingRepository
from .weights import WeightStorage, freeze_weight

logger = logging.getLogger(__name__)


class TrainingService:
    def __init__(
        self,
        repository: InMemoryTrainingRepository | PostgresTrainingRepository,
        storage: TrainingDatasetStorage,
        cluster: ClusterAlgorithmManagerGateway,
        project_id: UUID,
        actor: str,
        algorithms=None,
        model_store=None,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._cluster = cluster
        self._project_id = project_id
        self._actor = actor
        self._algorithms = algorithms
        self._model_store = model_store
        self.weights = WeightStorage(storage.root, project_id)

    # 数据集 ---------------------------------------------------------------

    def import_dataset(
        self,
        stream: BinaryIO,
        filename: str,
        name: str | None,
        config: SplitConfig | None = None,
        parent_id: UUID | None = None,
    ) -> TrainingDataset:
        parent = self.get_dataset(parent_id) if parent_id else None
        config = config or SplitConfig()
        dataset_id = uuid4()
        imported = self._storage.import_archive(stream, filename, dataset_id, config)
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
        dataset = replace(
            dataset,
            family_id=(parent.family_id or parent.id) if parent else dataset.id,
            parent_id=parent_id,
            source_sha256=imported.source_sha256,
            split_config=config.model_dump(),
        )
        try:
            dataset = self._repository.add_dataset(dataset)
        except Exception:
            self._storage.delete(dataset_id)
            raise
        return dataset

    def new_version(self, dataset_id: UUID, config: SplitConfig) -> TrainingDataset:
        dataset = self.get_dataset(dataset_id)
        source = self.dataset_archive(dataset.id, source=True)
        with source.open("rb") as stream:
            return self.import_dataset(stream, "dataset.zip", dataset.name, config, dataset.id)

    def dataset_archive(self, dataset_id: UUID, *, source: bool = False) -> Path:
        dataset = self.get_dataset(dataset_id)
        path = self._storage.source_path(dataset.id) if source else self._storage.path(dataset.id)
        if not path.is_file():
            raise ApplicationError(
                "DATASET_FILE_NOT_FOUND",
                "此历史版本未保留原包，请下载规范化包后重新上传" if source else "数据集文件不存在",
                404,
            )
        return path

    def dataset_images(
        self,
        dataset_id: UUID,
        split: str | None,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        self.get_dataset(dataset_id)
        items = self._storage.index(dataset_id)["images"]
        if split:
            items = [item for item in items if item["split"] == split]
        return {"total": len(items), "items": items[offset : offset + limit]}

    def dataset_report(self, dataset_id: UUID) -> dict[str, Any]:
        dataset = self.get_dataset(dataset_id)
        return {
            "dataset": dataset.snapshot(),
            "stats": dataset.stats,
            "index": self._storage.index(dataset_id),
        }

    def dataset_preview(self, dataset_id: UUID, image_id: int) -> bytes:
        self.get_dataset(dataset_id)
        return self._storage.preview(dataset_id, image_id)

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

    def import_weight(self, stream: BinaryIO, filename: str) -> dict:
        return self.weights.upload(stream, filename, self._actor)

    def list_sources(self) -> list[dict[str, Any]]:
        items = [
            {
                "source": {"kind": "upload", "reference": item["id"]},
                "label": item["name"],
                "sha256": item["sha256"],
                "validation": item["validation"],
            }
            for item in self.weights.list()
        ]
        if self._algorithms is not None:
            for item in self._algorithms.list():
                if (
                    item.project_id == self._project_id
                    and (item.lineage or {}).get("files", {}).get("model.pt")
                    or item.project_id == self._project_id
                    and (item.lineage or {}).get("files", {}).get("model.pth")
                ):
                    items.append(
                        {
                            "source": {"kind": "algorithm", "reference": str(item.id)},
                            "label": f"{item.manifest.name} v{item.manifest.version}",
                            "architecture": (item.lineage or {}).get("base_model"),
                        }
                    )
        for run in self.list_runs():
            if run.status in ACTIVE_STATUSES:
                continue
            for checkpoint in ("best", "last"):
                if f"weights/{checkpoint}.pt" in run.artifacts:
                    items.append(
                        {
                            "source": {
                                "kind": "run",
                                "reference": str(run.id),
                                "checkpoint": checkpoint,
                            },
                            "label": f"{run.name} · {checkpoint}.pt",
                            "architecture": (run.progress or {}).get("architecture") or run.model,
                        }
                    )
        return items

    def _resolve_source(self, source: TrainingSource, dataset, model, parameters):
        snapshot = source.model_dump(mode="json")
        if source.kind == "builtin":
            if model not in {item.key for item in TRAINING_MODELS}:
                raise ApplicationError("MODEL_INVALID", "不支持的预训练模型")
            return {**snapshot, "label": model}, None, parameters, model
        if source.mode != "resume" and parameters.get("pretrained") is False:
            raise ApplicationError("INPUT_INVALID", "自定义权重微调必须启用 pretrained", 422)
        expected = None
        if source.kind == "upload":
            model = "custom.pt"
            weight = self.weights.get(source.reference)
            path = self.weights.path(source.reference)
            label, expected = weight["name"], weight["sha256"]
        elif source.kind == "algorithm":
            model = "custom.pt"
            item = self._algorithms.get(source.reference) if self._algorithms else None
            if item is None or item.project_id != self._project_id:
                raise ApplicationError("MODEL_NOT_FOUND", "来源模型不存在", 404)
            filename = (
                "model.pth" if is_faster((item.lineage or {}).get("base_model")) else "model.pt"
            )
            recorded = (item.lineage or {}).get("files", {}).get(filename)
            if not recorded or self._model_store is None:
                raise ApplicationError("MODEL_INVALID", "此版本没有可微调的受管权重", 409)
            path = self._model_store.directory(item.id) / filename
            label, expected = f"{item.manifest.name} v{item.manifest.version}", recorded["sha256"]
            snapshot["architecture"] = (item.lineage or {}).get("base_model")
        else:
            parent = self.get_run(source.reference)
            if parent.status in ACTIVE_STATUSES:
                raise ApplicationError("TRAINING_ACTIVE", "请等待来源训练结束后再使用权重", 409)
            label = f"{parent.name} · {source.checkpoint}.pt"
            model = parent.model
            snapshot["architecture"] = (parent.progress or {}).get("architecture") or parent.model
            if source.mode == "resume":
                checkpoint = (parent.progress or {}).get("checkpoint") or {}
                if not checkpoint.get("resumable"):
                    raise ApplicationError(
                        "RESUME_UNAVAILABLE",
                        checkpoint.get("reason") or "此历史训练没有完整恢复状态，请使用权重微调",
                        409,
                    )
                if (
                    checkpoint.get("protocol") != protocol_for(snapshot.get("architecture"))
                    or checkpoint.get("dataset_sha256") != dataset.sha256
                ):
                    raise ApplicationError(
                        "RESUME_UNAVAILABLE", "恢复状态的协议或数据内容不匹配", 409
                    )
                if (
                    parent.dataset_id != dataset.id
                    or (parent.progress or {}).get("dataset", {}).get("sha256") != dataset.sha256
                ):
                    raise ApplicationError(
                        "RESUME_DATASET_CHANGED", "续训必须使用原数据集版本", 422
                    )
                changed = {
                    key
                    for key, value in parameters.items()
                    if key
                    not in (
                        FASTER_RESUME_OVERRIDES
                        if is_faster(snapshot.get("architecture"))
                        else RESUME_OVERRIDES
                    )
                    and value != parent.parameters.get(key)
                }
                if changed:
                    raise ApplicationError(
                        "RESUME_PARAMETERS_LOCKED",
                        f"续训不能修改 {', '.join(sorted(changed))}；请新建微调训练",
                        422,
                    )
                parameters = {**parent.parameters, **parameters}
                snapshot["checkpoint_state"] = checkpoint
                expected = checkpoint["sha256"]
                path = self.artifact(parent.id, checkpoint["file"])
            else:
                path = self.artifact(parent.id, f"weights/{source.checkpoint}.pt")
        return (
            {**snapshot, "label": label, "sha256": expected},
            path,
            parameters,
            model or "custom.pt",
        )

    def create_run(
        self,
        *,
        name: str,
        dataset_id: UUID,
        model: str,
        parameters: dict[str, Any],
        node_id: str,
        gpu_device_ids: tuple[str, ...],
        source: TrainingSource | None = None,
        architecture: str | None = None,
        operation: str = "train",
        verification: ExportVerification | None = None,
    ) -> TrainingRun:
        source = source or TrainingSource()
        if operation not in {"train", "val", "export"} or (
            operation != "train" and source.mode == "resume"
        ):
            raise ApplicationError("INPUT_INVALID", "不支持的任务模式或恢复方式", 422)
        dataset = self.get_dataset(dataset_id)
        snapshot, weight_path, parameters, model = self._resolve_source(
            source,
            dataset,
            model,
            parameters,
        )
        if architecture is not None:
            if architecture not in {item.key for item in TRAINING_MODELS}:
                raise ApplicationError("MODEL_INVALID", "不支持的算法或模型规格", 422)
            known = snapshot.get("architecture") or model
            known = known if known in {item.key for item in TRAINING_MODELS} else None
            if known and known != architecture:
                raise ApplicationError("MODEL_MISMATCH", "所选算法规格与来源权重不一致", 422)
            snapshot["expected_model"] = architecture
        elif (snapshot.get("architecture") or model) in {item.key for item in TRAINING_MODELS}:
            architecture = snapshot.get("architecture") or model
        if is_faster(architecture):
            model = FASTER_MODEL
        submitted = parameters.copy()
        try:
            parameters = normalize_for(architecture or model, operation, parameters)
            if is_faster(model):
                if len(gpu_device_ids) > 1:
                    raise ValueError("Faster R-CNN 当前支持单 GPU 或 CPU")
                if parameters["device"] == "cpu" and gpu_device_ids:
                    raise ValueError("CPU 执行不能同时指定 GPU")
            else:
                validate_placement(parameters, len(gpu_device_ids))
        except ValueError as exc:
            raise ApplicationError("INPUT_INVALID", str(exc), 422) from exc
        if any(index >= len(dataset.class_names) for index in parameters.get("classes") or []):
            raise ApplicationError("INPUT_INVALID", "classes 包含数据集中不存在的类别编号", 422)
        verification = verification or ExportVerification()
        if operation == "export":
            try:
                verified = verification_parameters(parameters, verification)
            except ValueError as exc:
                raise ApplicationError("INPUT_INVALID", str(exc), 422) from exc
            if any(index >= len(dataset.class_names) for index in verified.get("classes") or []):
                raise ApplicationError("INPUT_INVALID", "验证 classes 超出数据集类别范围", 422)
        if operation in {"val", "export"}:
            split = (verified if operation == "export" else parameters).get("split", "val")
            if not dataset.stats.get("splits", {}).get(split, {}).get("images"):
                raise ApplicationError("INPUT_INVALID", f"数据集没有 {split} 划分图片", 422)
            if (
                operation == "export"
                and not parameters["dynamic"]
                and (dataset.stats["splits"][split]["images"] % parameters["batch"])
            ):
                raise ApplicationError(
                    "INPUT_INVALID",
                    "静态 ONNX 的验证图片数须整除 batch；请选择动态导出或 batch=1",
                    422,
                )
        client = self._cluster.node_client(node_id)
        cpu = is_faster(model) and parameters.get("device") == "cpu"
        gpus = client.gpus() if operation != "export" and not cpu else []
        if operation == "export" and gpu_device_ids:
            raise ApplicationError("INPUT_INVALID", "ONNX 导出和对比验证当前使用 CPU", 422)
        if not gpus and operation != "export" and not cpu:
            raise ApplicationError(
                "GPU_UNAVAILABLE", f"服务器「{client.node_name}」没有检测到 GPU，无法训练", 409
            )
        available = {str(gpu["index"]) for gpu in gpus}
        if not set(gpu_device_ids) <= available:
            raise ApplicationError("GPU_UNAVAILABLE", "所选 GPU 不存在，请刷新后重新选择", 409)
        if not gpu_device_ids and operation != "export" and not cpu:
            # 未指定 GPU：选空闲显存最多的一张
            best = max(
                gpus,
                key=lambda gpu: (
                    int(gpu.get("memory_total_mb", 0)) - int(gpu.get("memory_used_mb", 0))
                ),
            )
            gpu_device_ids = (str(best["index"]),)
        if (
            operation != "export"
            and not cpu
            and (source.kind != "builtin" or operation == "val")
            and len(gpu_device_ids) != 1
        ):
            raise ApplicationError("INPUT_INVALID", "自定义权重与续训当前需要选择一张 GPU", 422)
        run = TrainingRun.queued(
            self._project_id,
            name.strip()[:120]
            or f"{dataset.name} · {model.removesuffix('.pt')}"
            + (" · 独立验证" if operation == "val" else ""),
            dataset.id,
            model,
            parameters,
            node_id,
            gpu_device_ids,
            self._actor,
        )
        run = replace(
            run,
            progress={
                "configuration": {
                    "contract_id": contract_for(model, operation)["id"],
                    "submitted": submitted,
                    "normalized": parameters,
                    "warnings": parameter_warnings(parameters)
                    if operation == "train" and not is_faster(model)
                    else [],
                },
                "dataset": dataset.snapshot(),
                "source": snapshot,
                "architecture": architecture,
                "operation": operation,
                **(
                    {"export_verification": verification.model_dump()}
                    if operation == "export"
                    else {}
                ),
            },
        )
        try:
            if weight_path:
                snapshot["sha256"] = freeze_weight(
                    weight_path,
                    self._storage.run_directory(run.id) / ".source" / "model.pt",
                    snapshot.get("sha256"),
                )
                if source.mode == "resume":
                    parent = self.get_run(source.reference)
                    if "weights/best.pt" in parent.artifacts:
                        snapshot["best_sha256"] = freeze_weight(
                            self.artifact(parent.id, "weights/best.pt"),
                            self._storage.run_directory(run.id) / ".source" / "best.pt",
                        )
            self._repository.add_run(run)
        except Exception:
            shutil.rmtree(self._storage.run_directory(run.id), ignore_errors=True)
            raise
        return run

    def list_runs(self, *, operation: str | None = None) -> list[TrainingRun]:
        return [
            run
            for run in self._repository.list_runs(self._project_id)
            if (
                (run.progress or {}).get("operation") == operation
                if operation
                else (run.progress or {}).get("operation") != "export"
            )
        ]

    def get_run(self, run_id: UUID) -> TrainingRun:
        run = self._repository.get_run(run_id)
        if run is None or run.project_id != self._project_id:
            raise ApplicationError("TRAINING_NOT_FOUND", "训练任务不存在", 404)
        return run

    def stop_run(self, run_id: UUID) -> TrainingRun:
        run = self.get_run(run_id)
        now = datetime.now(UTC)
        if run.status is TrainingStatus.QUEUED and self._repository.update(
            run.id,
            {TrainingStatus.QUEUED},
            status=TrainingStatus.STOPPED,
            finished_at=now,
        ):
            return self.get_run(run.id)
        if not self._repository.update(
            run.id,
            {TrainingStatus.PREPARING, TrainingStatus.RUNNING},
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

    def check_delete(self, run_id: UUID) -> None:
        run = self.get_run(run_id)
        if run.status in ACTIVE_STATUSES:
            raise ApplicationError("TRAINING_ACTIVE", "任务正在运行，请先停止后再删除", 409)
        # Source weights are normally frozen at creation. Protect legacy/live references
        # when the independent copy is absent; copied models and datasets are retained.
        for child in self._repository.list_runs(self._project_id):
            source = (child.progress or {}).get("source") or {}
            if source.get("kind") != "run" or str(source.get("reference")) != str(run_id):
                continue
            if child.status not in ACTIVE_STATUSES:
                continue
            frozen = self._storage.run_directory(child.id) / ".source" / "model.pt"
            if not frozen.is_file():
                raise ApplicationError("TRAINING_IN_USE", f"任务“{child.name}”仍引用此权重", 409)

        if self._algorithms is not None and self._model_store is not None:
            for model in self._algorithms.list():
                lineage = model.lineage or {}
                if model.project_id != self._project_id:
                    continue
                if lineage.get("training_run_id") != str(run_id):
                    continue
                files = self._model_store.files(model)
                if not files or any(not file.path.is_file() for file in files):
                    raise ApplicationError(
                        "TRAINING_IN_USE",
                        f"已发布模型“{model.manifest.name}”的独立文件不完整，请先修复",
                        409
                    )

    def delete_run(self, run_id: UUID) -> None:
        self.check_delete(run_id)
        run = self.get_run(run_id)
        try:
            self._cluster.node_client(run.node_id).request(
                "DELETE", f"/trainings/{run.id}", timeout=30
            )
        except ApplicationError as exc:
            if exc.status_code != 404:
                raise ApplicationError(
                    "TRAINING_CLEANUP_FAILED", "执行节点清理失败，记录已保留，请稍后重试", 502
                ) from exc
        directory = self._storage.run_directory(run.id)
        if directory.exists():
            shutil.rmtree(directory)
        self._repository.delete_run(run.id)

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
        """原始产物与报告 ZIP，按记录版本缓存，允许下载未完成任务的快照。"""
        run = self.get_run(run_id)
        from .reports import write_bundle

        revision = run.updated_at.strftime("%Y%m%d%H%M%S%f")
        target = self._storage.run_directory(run.id) / f".report-v2-{revision}.zip"
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f".results-{uuid4().hex}.zip")
        try:
            write_bundle(self, run, staging)
            staging.replace(target)
        finally:
            staging.unlink(missing_ok=True)
        return target

    def evaluation_report(self, run_id: UUID) -> dict:
        self.get_run(run_id)
        path = self.artifact(run_id, "evaluation.json")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ApplicationError("REPORT_INVALID", "评估报告读取失败", 409) from exc
