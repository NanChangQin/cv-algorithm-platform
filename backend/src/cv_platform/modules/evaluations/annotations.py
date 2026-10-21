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
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from ...core.errors import ApplicationError
from ..assets.domain.entities import ImageAsset
from .domain import DatasetItem

HEADER_NAMES = {"file", "filename", "file_name", "image", "文件名"}


def _invalid(message: str) -> ApplicationError:
    return ApplicationError("ANNOTATIONS_INVALID", f"标注文件无法解析：{message}", 400)


def _basename(name: str) -> str:
    return PureWindowsPath(PurePosixPath(name).name).name


def _bbox(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise _invalid("检测框必须是 4 个数字")
    try:
        x1, y1, x2, y2 = (float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise _invalid("检测框必须是 4 个数字") from exc
    if x2 <= x1 or y2 <= y1:
        raise _invalid("检测框的右下角必须在左上角右下方（格式为 [x1, y1, x2, y2]）")
    return [x1, y1, x2, y2]


def _ground_truth(task_type: str, entry: dict[str, Any]) -> dict[str, Any]:
    if task_type == "object_detection":
        detections = entry.get("detections")
        if not isinstance(detections, list):
            raise _invalid("目标检测样本需要 detections 列表")
        return {"detections": [
            {"label": str(item["label"]), "bbox": _bbox(item.get("bbox"))}
            for item in detections
            if isinstance(item, dict) and "label" in item
        ]}
    if task_type == "classification":
        if "label" not in entry:
            raise _invalid("图像分类样本需要 label")
        return {"label": str(entry["label"])}
    if task_type == "segmentation":
        labels = entry.get("labels")
        if not isinstance(labels, list):
            raise _invalid("图像分割样本需要 labels 列表")
        return {"labels": [str(item) for item in labels]}
    if task_type == "ocr":
        if "text" not in entry:
            raise _invalid("文字识别样本需要 text")
        return {"text": str(entry["text"])}
    keypoints = entry.get("keypoints")
    if not isinstance(keypoints, list):
        raise _invalid("姿态估计样本需要 keypoints 列表")
    truth: dict[str, Any] = {"keypoints": [
        {"name": str(item["name"]), "x": float(item["x"]), "y": float(item["y"])}
        for item in keypoints
        if isinstance(item, dict) and {"name", "x", "y"} <= item.keys()
    ]}
    if "threshold" in entry:
        truth["threshold"] = float(entry["threshold"])
    return truth


def _from_coco(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    categories = {item["id"]: str(item["name"]) for item in document.get("categories", [])}
    files = {item["id"]: _basename(str(item["file_name"])) for item in document.get("images", [])}
    entries: dict[str, dict[str, Any]] = {name: {"detections": []} for name in files.values()}
    for annotation in document.get("annotations", []):
        name = files.get(annotation.get("image_id"))
        if name is None:
            continue
        x, y, width, height = (float(value) for value in annotation["bbox"])
        label = categories.get(annotation.get("category_id"), str(annotation.get("category_id")))
        entries[name]["detections"].append({"label": label, "bbox": [x, y, x + width, y + height]})
    return entries


def _from_csv(task_type: str, text: str) -> dict[str, dict[str, Any]]:
    if task_type not in {"classification", "ocr"}:
        raise _invalid("CSV 只支持图像分类和文字识别，其他类型请使用 JSON")
    field_name = "label" if task_type == "classification" else "text"
    entries: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(csv.reader(io.StringIO(text))):
        if not row or not row[0].strip():
            continue
        if index == 0 and row[0].strip().lower() in HEADER_NAMES:
            continue
        if len(row) < 2:
            column = "标签" if field_name == "label" else "文本"
            raise _invalid(f"第 {index + 1} 行需要“文件名,{column}”两列")
        entries[_basename(row[0].strip())] = {field_name: ",".join(row[1:]).strip()}
    return entries


def parse_annotations(
    task_type: str, filename: str, text: str, assets: list[ImageAsset]
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
                entries[_basename(str(entry["file"]))] = entry
        else:
            raise _invalid("请使用 COCO 格式，或 {\"items\": [{\"file\": ...}]}")

    by_name = {asset.original_name: asset for asset in assets}
    by_lower = {asset.original_name.lower(): asset for asset in assets}
    items: list[DatasetItem] = []
    unmatched: list[str] = []
    used: set[str] = set()
    for name, entry in entries.items():
        asset = by_name.get(name) or by_lower.get(name.lower())
        if asset is None:
            unmatched.append(name)
            continue
        if str(asset.id) in used:
            continue
        used.add(str(asset.id))
        items.append(DatasetItem(asset.id, _ground_truth(task_type, entry)))
    unlabeled = [asset.original_name for asset in assets if str(asset.id) not in used]
    return items, unmatched, unlabeled
