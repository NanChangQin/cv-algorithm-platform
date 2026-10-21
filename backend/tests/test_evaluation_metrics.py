from cv_algorithm_sdk import AlgorithmResult
from pydantic import TypeAdapter

from cv_platform.modules.evaluations.metrics import aggregate_metrics, evaluate_result

RESULT = TypeAdapter(AlgorithmResult)


def result_payload(result_type: str, data: dict) -> dict:
    return {
        "schema_version": "1.0",
        "request_id": "evaluation-test",
        "type": result_type,
        "algorithm": {"id": "test", "version": "1.0.0"},
        "input": {"width": 100, "height": 100},
        "timing": {"preprocess_ms": 1, "inference_ms": 2, "postprocess_ms": 1},
        "data": data,
    }


def test_detection_metrics_match_by_label_and_iou() -> None:
    result = RESULT.validate_python(result_payload("object_detection", {"detections": [
        {"label": "cat", "score": 0.9, "bbox": [10, 10, 50, 50]},
        {"label": "dog", "score": 0.7, "bbox": [60, 60, 80, 80]},
    ]}))
    metrics = evaluate_result(result, {"detections": [
        {"label": "cat", "bbox": [10, 10, 50, 50]},
        {"label": "bird", "bbox": [60, 60, 80, 80]},
    ]})
    assert metrics["precision_iou50"] == 0.5
    assert metrics["recall_iou50"] == 0.5


def test_classification_and_ocr_metrics() -> None:
    classification = RESULT.validate_python(result_payload("classification", {"predictions": [
        {"label": "dog", "score": 0.8}, {"label": "cat", "score": 0.2},
    ]}))
    assert evaluate_result(classification, {"label": "cat"}) == {
        "top1_accuracy": 0.0, "top5_accuracy": 1.0,
    }

    ocr = RESULT.validate_python(result_payload("ocr", {"texts": [
        {"text": "算法平台", "score": 0.9, "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]},
    ]}))
    assert evaluate_result(ocr, {"text": "算法平合"})["character_accuracy"] == 0.75


def test_aggregate_metrics_averages_available_values() -> None:
    assert aggregate_metrics([{"accuracy": 1.0}, {"accuracy": 0.0, "latency": 10.0}]) == {
        "accuracy": 0.5, "latency": 10.0,
    }
