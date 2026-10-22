from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from cv_algorithm_sdk import AlgorithmResult


def evaluate_result(result: AlgorithmResult, ground_truth: dict[str, Any]) -> dict[str, float]:
    if result.type == "object_detection":
        return _detection_metrics(result.data.detections, ground_truth.get("detections", []))
    if result.type == "classification":
        expected = str(ground_truth.get("label", ""))
        labels = [item.label for item in result.data.predictions]
        return {
            "top1_accuracy": float(bool(labels) and labels[0] == expected),
            "top5_accuracy": float(expected in labels[:5]),
        }
    if result.type == "segmentation":
        predicted = {item.label for item in result.data.segments}
        expected = {str(item) for item in ground_truth.get("labels", [])}
        return _set_metrics(predicted, expected)
    if result.type == "ocr":
        predicted = "".join(item.text for item in result.data.texts)
        expected = str(ground_truth.get("text", ""))
        distance = _edit_distance(predicted, expected)
        denominator = max(1, len(expected))
        return {
            "character_accuracy": max(0.0, 1 - distance / denominator),
            "edit_distance": float(distance),
        }
    if result.type == "pose_estimation":
        expected = ground_truth.get("keypoints", [])
        predicted = result.data.instances[0].keypoints if result.data.instances else []
        longest_side = max(result.input.width, result.input.height)
        threshold = float(ground_truth.get("threshold", 0.05)) * longest_side
        predicted_by_name = {}
        for keypoint in predicted:
            if (
                keypoint.name not in predicted_by_name
                or keypoint.score > predicted_by_name[keypoint.name].score
            ):
                predicted_by_name[keypoint.name] = keypoint
        correct = 0
        compared = len(expected)
        for target in expected:
            keypoint = predicted_by_name.get(str(target["name"]))
            if keypoint is None:
                continue
            distance = math.hypot(keypoint.x - float(target["x"]), keypoint.y - float(target["y"]))
            correct += distance <= threshold
        return {
            "pck": correct / compared if compared else 0.0,
            "keypoints_compared": float(compared),
        }
    raise ValueError(f"unsupported result type: {result.type}")


def aggregate_metrics(samples: Sequence[dict[str, float]]) -> dict[str, float]:
    keys = sorted({key for sample in samples for key in sample})
    averages = {}
    for key in keys:
        values = [sample[key] for sample in samples if key in sample]
        averages[key] = round(sum(values) / len(values), 6)
    return averages


def _detection_metrics(predictions, expected: list[dict[str, Any]]) -> dict[str, float]:
    unmatched = set(range(len(expected)))
    true_positive = 0
    for prediction in sorted(predictions, key=lambda item: item.score, reverse=True):
        candidates = [
            (index, _iou(prediction.bbox, expected[index]["bbox"]))
            for index in unmatched
            if prediction.label == expected[index].get("label")
        ]
        if not candidates:
            continue
        index, overlap = max(candidates, key=lambda item: item[1])
        if overlap >= 0.5:
            unmatched.remove(index)
            true_positive += 1
    false_positive = len(predictions) - true_positive
    false_negative = len(expected) - true_positive
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    return {
        "precision_iou50": precision,
        "recall_iou50": recall,
        "f1_iou50": 2 * precision * recall / max(1e-12, precision + recall),
        "true_positive": float(true_positive),
        "false_positive": float(false_positive),
        "false_negative": float(false_negative),
    }


def _iou(left, right) -> float:
    lx1, ly1, lx2, ly2 = map(float, getattr(left, "root", left))
    rx1, ry1, rx2, ry2 = map(float, getattr(right, "root", right))
    intersection = max(0.0, min(lx2, rx2) - max(lx1, rx1)) * max(0.0, min(ly2, ry2) - max(ly1, ry1))
    left_area = max(0.0, lx2 - lx1) * max(0.0, ly2 - ly1)
    right_area = max(0.0, rx2 - rx1) * max(0.0, ry2 - ry1)
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _set_metrics(predicted: set[str], expected: set[str]) -> dict[str, float]:
    matched = len(predicted & expected)
    precision = matched / max(1, len(predicted))
    recall = matched / max(1, len(expected))
    return {
        "label_precision": precision,
        "label_recall": recall,
        "label_f1": 2 * precision * recall / max(1e-12, precision + recall),
    }


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, 1):
        current = [left_index]
        for right_index, right_character in enumerate(right, 1):
            substitution = previous[right_index - 1] + (left_character != right_character)
            current.append(min(current[-1] + 1, previous[right_index] + 1, substitution))
        previous = current
    return previous[-1]


# 每类任务用来排序“最差样本”和在列表里突出显示的主指标
PRIMARY_METRIC = {
    "object_detection": "f1_iou50",
    "classification": "top1_accuracy",
    "segmentation": "label_f1",
    "ocr": "character_accuracy",
    "pose_estimation": "pck",
}


def summarize(task_type: str, samples: Sequence[dict[str, float]]) -> dict[str, float]:
    """数据集层面的指标。检测按所有图片累计的 TP/FP/FN 计算（不是逐图平均），其余取逐图平均。"""
    if not samples:
        return {}
    summary = aggregate_metrics(samples)
    if task_type == "object_detection":
        true_positive = sum(sample.get("true_positive", 0.0) for sample in samples)
        false_positive = sum(sample.get("false_positive", 0.0) for sample in samples)
        false_negative = sum(sample.get("false_negative", 0.0) for sample in samples)
        precision = true_positive / max(1.0, true_positive + false_positive)
        recall = true_positive / max(1.0, true_positive + false_negative)
        summary.update(
            precision_iou50=round(precision, 6),
            recall_iou50=round(recall, 6),
            f1_iou50=round(2 * precision * recall / max(1e-12, precision + recall), 6),
            true_positive=true_positive,
            false_positive=false_positive,
            false_negative=false_negative,
        )
    return summary
