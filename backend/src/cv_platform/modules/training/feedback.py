from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ...core.errors import ApplicationError
from ..assets.domain.repositories import AssetRepository
from ..assets.infrastructure.local_storage import LocalAssetStorage
from ..tasks.domain.repositories import TaskRepository
from .datasets import SplitConfig
from .domain import TrainingDataset
from .service import TrainingService


class FeedbackBox(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=64)
    bbox: tuple[float, float, float, float]


class FeedbackSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    boxes: list[FeedbackBox] | None = None


class DatasetFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    samples: list[FeedbackSample] = Field(min_length=1, max_length=200)
    target_split: Literal["train", "val", "test"] = "train"
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    split_config: SplitConfig = Field(default_factory=SplitConfig)


def _yolo_line(
    class_index: int,
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
) -> str:
    x1, y1, x2, y2 = bbox
    x1 = max(0.0, min(float(width), float(x1)))
    y1 = max(0.0, min(float(height), float(y1)))
    x2 = max(0.0, min(float(width), float(x2)))
    y2 = max(0.0, min(float(height), float(y2)))
    if x2 <= x1 or y2 <= y1 or width <= 0 or height <= 0:
        raise ApplicationError("FEEDBACK_BBOX_INVALID", "回流标注框坐标不合法")
    cx = ((x1 + x2) / 2.0) / width
    cy = ((y1 + y2) / 2.0) / height
    bw = (x2 - x1) / width
    bh = (y2 - y1) / height
    return f"{class_index} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def feedback_to_dataset(
    training_service: TrainingService,
    dataset_id: UUID,
    request: DatasetFeedbackRequest,
    *,
    tasks: TaskRepository,
    assets: AssetRepository,
    asset_storage: LocalAssetStorage,
    project_id: UUID,
) -> TrainingDataset:
    dataset = training_service.get_dataset(dataset_id)
    class_to_index = {name: idx for idx, name in enumerate(dataset.class_names)}
    # 始终基于当前数据集版本的规范化包追加样本，确保已有划分不漂移且路径统一在顶层 images/labels
    base_archive = training_service.dataset_archive(dataset.id, source=False)

    seen_tasks: set[UUID] = set()
    prepared: list[tuple[str, bytes, str]] = []
    for sample in request.samples:
        if sample.task_id in seen_tasks:
            continue
        seen_tasks.add(sample.task_id)
        task = tasks.get(sample.task_id)
        if task is None or task.project_id != project_id:
            raise ApplicationError("TASK_NOT_FOUND", f"推理任务不存在：{sample.task_id}", 404)
        if task.asset_id is None:
            raise ApplicationError("ASSET_NOT_FOUND", "该任务未关联平台图片资源，无法回流", 409)
        asset = assets.get(task.asset_id)
        if asset is None or asset.project_id != project_id:
            raise ApplicationError("ASSET_NOT_FOUND", "回流任务对应的图片资源不存在", 404)
        image_path = asset_storage.resolve(asset.storage_key)
        suffix = Path(asset.original_name).suffix.lower() or image_path.suffix.lower() or ".jpg"
        stem = f"feedback_{task.id.hex[:12]}"

        lines: list[str] = []
        if sample.boxes is not None:
            for box in sample.boxes:
                if box.label not in class_to_index:
                    raise ApplicationError(
                        "FEEDBACK_LABEL_UNKNOWN",
                        f"类别「{box.label}」不在目标数据集类别列表中：{list(dataset.class_names)}",
                    )
                lines.append(
                    _yolo_line(class_to_index[box.label], box.bbox, asset.width, asset.height)
                )
        else:
            if task.result is None or getattr(task.result, "type", None) != "object_detection":
                raise ApplicationError(
                    "FEEDBACK_RESULT_UNSUPPORTED",
                    "仅已完成的目标检测任务或显式提供标注框的任务支持回流到检测数据集",
                    409,
                )
            for det in task.result.data.detections:
                if float(det.score) < request.min_confidence:
                    continue
                if det.label not in class_to_index:
                    raise ApplicationError(
                        "FEEDBACK_LABEL_UNKNOWN",
                        f"预测类别「{det.label}」不在目标数据集类别列表中：{list(dataset.class_names)}",
                    )
                raw_bbox = getattr(det.bbox, 'root', det.bbox)
                bbox = tuple(float(v) for v in raw_bbox)
                lines.append(
                    _yolo_line(
                        class_to_index[det.label],
                        (bbox[0], bbox[1], bbox[2], bbox[3]),
                        asset.width,
                        asset.height,
                    )
                )

        prepared.append((f"{stem}{suffix}", image_path.read_bytes(), "\n".join(lines)))

    buffer = io.BytesIO()
    with zipfile.ZipFile(base_archive, "r") as src, zipfile.ZipFile(
        buffer, "w", compression=zipfile.ZIP_DEFLATED
    ) as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        existing_names = set(dst.namelist())
        for image_name, image_bytes, label_text in prepared:
            img_arc = f"images/{request.target_split}/{image_name}"
            lbl_arc = f"labels/{request.target_split}/{Path(image_name).stem}.txt"
            if img_arc not in existing_names:
                dst.writestr(img_arc, image_bytes)
            if lbl_arc not in existing_names:
                dst.writestr(lbl_arc, (label_text + "\n") if label_text else "")

    buffer.seek(0)
    return training_service.import_dataset(
        buffer,
        f"{dataset.name}-feedback.zip",
        dataset.name,
        request.split_config,
        parent_id=dataset.id,
    )