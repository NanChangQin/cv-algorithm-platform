"""把训练得到的权重发布为推理用的算法版本，并记录它的来源，方便对比和回滚。"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Literal
from uuid import UUID, uuid4

import yaml
from cv_algorithm_sdk import AlgorithmManifest

from ...core.errors import ApplicationError
from ..algorithms.domain.entities import AlgorithmVersion
from ..algorithms.domain.repositories import AlgorithmRepository
from ..algorithms.infrastructure.model_store import ModelStore
from .datasets import TrainingDatasetStorage
from .domain import TrainingRun, TrainingStatus
from .repository import InMemoryTrainingRepository, PostgresTrainingRepository

KEY = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
FITNESS = "metrics/mAP50-95(B)"


def inference_manifest(
    key: str, name: str, version: str, description: str, image_size: int
) -> AlgorithmManifest:
    """与 examples/yolo 的推理参数一致，默认推理尺寸取训练时的图片尺寸。"""
    return AlgorithmManifest.model_validate({
        "schema_version": "1.0",
        "id": key,
        "name": name,
        "version": version,
        "description": description,
        "task_type": "object_detection",
        "runtime": {"framework": "pytorch", "device": "gpu", "min_memory_mb": 2048},
        "input": {"media_types": ["image/jpeg", "image/png"]},
        "output": {"type": "object_detection"},
        "parameters": {
            "confidence": {
                "type": "number", "default": 0.25, "minimum": 0, "maximum": 1, "step": 0.01,
                "title": "置信度阈值", "description": "仅保留得分不低于该值的检测框",
            },
            "iou_threshold": {
                "type": "number", "default": 0.45, "minimum": 0, "maximum": 1, "step": 0.01,
                "title": "NMS IoU 阈值", "description": "控制重叠检测框的合并强度",
            },
            "max_detections": {
                "type": "integer", "default": 300, "minimum": 1, "maximum": 1000, "step": 1,
                "title": "最大检测数量", "description": "单张图片最多返回的目标框数量",
            },
            "image_size": {
                "type": "integer", "default": image_size, "minimum": 32, "maximum": 2048,
                "step": 32, "title": "推理输入尺寸",
                "description": "默认与训练尺寸一致；较大尺寸有利于小目标，但更慢",
            },
            "agnostic_nms": {
                "type": "boolean", "default": False, "title": "类别无关 NMS",
                "description": "开启后，不同类别的重叠框也会互相抑制",
            },
        },
    })


class ModelPublisher:
    def __init__(
        self,
        training: InMemoryTrainingRepository | PostgresTrainingRepository,
        storage: TrainingDatasetStorage,
        algorithms: AlgorithmRepository,
        model_store: ModelStore,
        inference_image: str,
        project_id: UUID,
        actor: str,
    ) -> None:
        self._training = training
        self._storage = storage
        self._algorithms = algorithms
        self._models = model_store
        self._image = inference_image
        self._project_id = project_id
        self._actor = actor

    def publish(
        self,
        run_id: UUID,
        *,
        checkpoint: Literal["best", "last"],
        key: str,
        name: str,
        version: str | None,
        description: str,
    ) -> AlgorithmVersion:
        run = self._training.get_run(run_id)
        if run is None or run.project_id != self._project_id:
            raise ApplicationError("TRAINING_NOT_FOUND", "训练任务不存在", 404)
        if run.status not in {TrainingStatus.COMPLETED, TrainingStatus.STOPPED}:
            raise ApplicationError("TRAINING_NOT_FINISHED", "训练结束后才能发布模型", 409)
        weights_name = f"weights/{checkpoint}.pt"
        weights = self._storage.run_directory(run.id) / weights_name
        if weights_name not in run.artifacts or not weights.is_file():
            raise ApplicationError(
                "CHECKPOINT_MISSING", f"这次训练没有保存 {checkpoint}.pt", 409
            )
        if not KEY.fullmatch(key):
            raise ApplicationError(
                "INPUT_INVALID", "模型 ID 只能用小写字母、数字和连字符，3 到 64 个字符"
            )
        family = [
            item for item in self._algorithms.list()
            if item.project_id == self._project_id and item.manifest.id == key
        ]
        if any(item.lineage is None for item in family):
            raise ApplicationError(
                "MODEL_KEY_TAKEN", "这个模型 ID 已被导入的算法使用，请换一个", 409
            )
        version = (version or "").strip() or next_version(item.manifest.version for item in family)
        if any(item.manifest.version == version for item in family):
            raise ApplicationError("ALGORITHM_VERSION_EXISTS", f"{key} 已有版本 {version}", 409)

        dataset = self._training.get_dataset(run.dataset_id)
        description = description.strip() or (
            f"由训练「{run.name}」的 {checkpoint}.pt 发布"
            + (f"，数据集「{dataset.name}」" if dataset else "")
        )
        try:
            manifest = inference_manifest(
                key, name.strip() or key, version, description[:1000],
                int(run.parameters.get("imgsz", 640)),
            )
        except ValueError as exc:
            raise ApplicationError("INPUT_INVALID", "版本号格式应为 1.2.3") from exc
        version_id = uuid4()
        files = self._models.publish(
            version_id, weights, yaml.safe_dump(
                manifest.model_dump(mode="json"), allow_unicode=True, sort_keys=False
            ),
        )
        algorithm = AlgorithmVersion.available(
            manifest, self._image, algorithm_id=version_id, project_id=self._project_id,
            created_by=self._actor,
        )
        algorithm = replace(
            algorithm,
            model_directory=f"trained/{version_id.hex}",
            lineage=lineage(run, dataset, checkpoint, files),
        )
        try:
            self._algorithms.add(algorithm)
        except ValueError as exc:
            self._models.delete(version_id)
            raise ApplicationError(
                "ALGORITHM_VERSION_EXISTS", f"{key} 已有版本 {version}", 409
            ) from exc
        return algorithm


def next_version(existing) -> str:
    """同一模型下一个补丁版本号；没有版本时从 1.0.0 开始。"""
    parsed = [tuple(int(part) for part in match.groups())
              for value in existing if (match := SEMVER.match(value))]
    if not parsed:
        return "1.0.0"
    major, minor, patch = max(parsed)
    return f"{major}.{minor}.{patch + 1}"


def lineage(run: TrainingRun, dataset, checkpoint: str, files: dict[str, Any]) -> dict[str, Any]:
    scored = [row for row in run.metrics if FITNESS in row]
    if checkpoint == "best" and scored:
        row = max(scored, key=lambda item: item[FITNESS])
    else:
        row = run.metrics[-1] if run.metrics else {}
    return {
        "training_run_id": str(run.id),
        "training_name": run.name,
        "dataset_id": str(run.dataset_id),
        "dataset_name": dataset.name if dataset else None,
        "class_names": list(dataset.class_names) if dataset else [],
        "base_model": run.model,
        "checkpoint": checkpoint,
        "epoch": int(row.get("epoch", 0)) or None,
        "epochs_trained": len(run.metrics),
        "parameters": run.parameters,
        "metrics": {
            "precision": row.get("metrics/precision(B)"),
            "recall": row.get("metrics/recall(B)"),
            "map50": row.get("metrics/mAP50(B)"),
            "map50_95": row.get(FITNESS),
        },
        "files": files,
    }
