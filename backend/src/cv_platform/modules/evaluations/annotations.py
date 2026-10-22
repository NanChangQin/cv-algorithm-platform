"""把标注文件解析成评测集样本，按文件名和已上传的图片对应。

支持三种格式：
- COCO JSON（目标检测）：images / annotations / categories，bbox 为 [x, y, 宽, 高]；
- 平台 JSON：{"items": [{"file": "a.jpg", ...标注字段}]}，五类任务都可以用；
- CSV（图像分类、文字识别）：每行“文件名,标签”或“文件名,文本”，可以有表头。
"""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from uuid import UUID

from ...core.errors import ApplicationError
from ..assets.domain.entities import ImageAsset
from .domain import DatasetItem

HEADER_NAMES = {"file", "filename", "file_name", "image", "文件名"}
AnnotationEntries = dict[str, tuple[str, dict[str, Any]]]


def _invalid(message: str) -> ApplicationError:
    return ApplicationError("ANNOTATIONS_INVALID", f"标注文件无法解析：{message}", 400)


def _basename(name: str) -> str:
    return PureWindowsPath(PurePosixPath(name).name).name


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _invalid("坐标和阈值必须是有限数字") from exc
    if isinstance(value, bool) or not math.isfinite(number):
        raise _invalid("坐标和阈值必须是有限数字")
    return number


def _label(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid("类别和关键点名称必须是非空字符串")
    return value


def _add_entry(entries: AnnotationEntries, name: Any, entry: dict[str, Any]) -> None:
    if not isinstance(name, str) or not name.strip() or not _basename(name):
        raise _invalid("样本需要非空文件名")
    key = _basename(name).lower()
    if key in entries:
        raise _invalid(f"文件名重复或忽略目录、大小写后重名：{name}")
    entries[key] = (_basename(name), entry)


def _bbox(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise _invalid("检测框必须是 4 个数字")
    x1, y1, x2, y2 = (_number(item) for item in value)
    if x2 <= x1 or y2 <= y1:
        raise _invalid("检测框的右下角必须在左上角右下方（格式为 [x1, y1, x2, y2]）")
    return [x1, y1, x2, y2]


def _ground_truth(task_type: str, entry: dict[str, Any]) -> dict[str, Any]:
    if task_type == "object_detection":
        detections = entry.get("detections")
        if not isinstance(detections, list):
            raise _invalid("目标检测样本需要 detections 列表")
        parsed = []
        for item in detections:
            if not isinstance(item, dict):
                raise _invalid("每个检测标注都需要 label 和 bbox")
            parsed.append({"label": _label(item.get("label")), "bbox": _bbox(item.get("bbox"))})
        return {"detections": parsed}
    if task_type == "classification":
        if "label" not in entry:
            raise _invalid("图像分类样本需要 label")
        return {"label": _label(entry["label"])}
    if task_type == "segmentation":
        labels = entry.get("labels")
        if not isinstance(labels, list):
            raise _invalid("图像分割样本需要 labels 列表")
        return {"labels": [_label(item) for item in labels]}
    if task_type == "ocr":
        if not isinstance(entry.get("text"), str):
            raise _invalid("文字识别样本需要 text")
        return {"text": str(entry["text"])}
    keypoints = entry.get("keypoints")
    if not isinstance(keypoints, list) or not keypoints:
        raise _invalid("姿态估计样本需要非空 keypoints 列表")
    points = []
    names = set()
    for item in keypoints:
        if not isinstance(item, dict):
            raise _invalid("每个关键点都需要 name、x 和 y")
        name = _label(item.get("name"))
        if name in names:
            raise _invalid(f"关键点名称重复：{name}")
        names.add(name)
        points.append({"name": name, "x": _number(item.get("x")), "y": _number(item.get("y"))})
    truth: dict[str, Any] = {"keypoints": points}
    if "threshold" in entry:
        threshold = _number(entry["threshold"])
        if threshold <= 0:
            raise _invalid("关键点阈值必须大于 0")
        truth["threshold"] = threshold
    return truth


def _from_coco(document: dict[str, Any]) -> AnnotationEntries:
    def records(key: str) -> list[dict]:
        value = document.get(key)
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise _invalid(f"COCO 的 {key} 必须是对象列表")
        return value

    def identifier(value: Any) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise _invalid("COCO 的 id 必须是整数")
        return value

    categories = {}
    for item in records("categories"):
        key = identifier(item.get("id"))
        if key in categories:
            raise _invalid("COCO category id 重复")
        categories[key] = _label(item.get("name"))
    files = {}
    entries: AnnotationEntries = {}
    for item in records("images"):
        key = identifier(item.get("id"))
        if key in files:
            raise _invalid("COCO image id 重复")
        name = item.get("file_name")
        _add_entry(entries, name, {"detections": []})
        files[key] = _basename(name).lower()
    for annotation in records("annotations"):
        image = identifier(annotation.get("image_id"))
        category = identifier(annotation.get("category_id"))
        if image not in files or category not in categories:
            raise _invalid("COCO 标注引用了不存在的图片或类别")
        box = annotation.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            raise _invalid("COCO bbox 必须为 [x, y, 宽, 高]")
        x, y, width, height = (_number(value) for value in box)
        entries[files[image]][1]["detections"].append({
            "label": categories[category], "bbox": _bbox([x, y, x + width, y + height]),
        })
    return entries


def _from_csv(task_type: str, text: str) -> AnnotationEntries:
    if task_type not in {"classification", "ocr"}:
        raise _invalid("CSV 只支持图像分类和文字识别，其他类型请使用 JSON")
    field_name = "label" if task_type == "classification" else "text"
    entries: AnnotationEntries = {}
    for index, row in enumerate(csv.reader(io.StringIO(text))):
        if not row or not row[0].strip():
            continue
        if index == 0 and row[0].strip().lower() in HEADER_NAMES:
            continue
        if len(row) < 2:
            column = "标签" if field_name == "label" else "文本"
            raise _invalid(f"第 {index + 1} 行需要“文件名,{column}”两列")
        _add_entry(entries, row[0].strip(), {field_name: ",".join(row[1:]).strip()})
    return entries


def parse_annotations(
    task_type: str, filename: str, text: str, assets: list[ImageAsset],
    asset_names: dict[UUID, str] | None = None,
) -> tuple[list[DatasetItem], list[str], list[str]]:
    """返回 (样本, 标注里有但没有对应图片的文件名, 已上传但没有标注的图片)。"""
    if filename.lower().endswith(".csv"):
        entries = _from_csv(task_type, text)
    else:
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _invalid(f"JSON 格式错误（第 {exc.lineno} 行）") from exc
        if isinstance(document, dict) and "images" in document and "annotations" in document:
            if task_type != "object_detection":
                raise _invalid("COCO 格式只用于目标检测")
            entries = _from_coco(document)
        elif isinstance(document, dict) and isinstance(document.get("items"), list):
            entries = {}
            for entry in document["items"]:
                if not isinstance(entry, dict) or "file" not in entry:
                    raise _invalid("items 里的每一项都需要 file 字段")
                _add_entry(entries, entry["file"], entry)
        else:
            raise _invalid("请使用 COCO 格式，或 {\"items\": [{\"file\": ...}]}")

    by_lower = {}
    for asset in assets:
        name = (asset_names or {}).get(asset.id, asset.original_name)
        if not name.strip():
            raise _invalid("图片文件名不能为空")
        key = _basename(name).lower()
        if key in by_lower and by_lower[key].id != asset.id:
            raise _invalid(f"上传的图片文件名有歧义：{name}")
        by_lower[key] = asset
    items: list[DatasetItem] = []
    unmatched: list[str] = []
    used: set[str] = set()
    for name, entry in entries.values():
        ground_truth = _ground_truth(task_type, entry)
        asset = by_lower.get(name.lower())
        if asset is None:
            unmatched.append(name)
            continue
        if str(asset.id) in used:
            continue
        used.add(str(asset.id))
        items.append(DatasetItem(asset.id, ground_truth))
    unlabeled = [
        _basename((asset_names or {}).get(asset.id, asset.original_name))
        for asset in assets if str(asset.id) not in used
    ]
    return items, unmatched, unlabeled
