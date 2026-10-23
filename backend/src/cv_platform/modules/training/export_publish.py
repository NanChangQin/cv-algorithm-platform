"""Publish verified ONNX artifacts into an independent immutable model version."""

import json
from dataclasses import replace
from uuid import uuid4

import yaml
from cv_algorithm_sdk import AlgorithmManifest
from cv_algorithm_sdk.export_artifacts import registered_artifacts
from cv_algorithm_sdk.runtime_contract import prediction_parameters

from ...core.errors import ApplicationError
from ..algorithms.domain.entities import AlgorithmVersion
from .domain import TrainingStatus
from .publish import KEY, next_version


def onnx_manifest(key, name, version, description, report):
    shape = report["input_shape"]
    if (len(shape) != 4 or shape[1] != 3
        or (shape[0] != 1 and not report["dynamic"])):
        raise ValueError("当前 ONNX 部署支持 NCHW、RGB、batch=1，请重新导出")
    parameters = prediction_parameters(shape[2])
    # This adapter implements the standard single-image detection pipeline.
    supported = {"confidence", "iou_threshold", "max_detections", "image_size",
                 "classes", "agnostic_nms"}
    parameters = {key: value for key, value in parameters.items() if key in supported}
    size = shape[2] if shape[2] == shape[3] else shape[2:4]
    parameters["image_size"]["default"] = size
    if not report["dynamic"]:
        parameters["image_size"]["json_schema"] = {"const": size}
        parameters["image_size"]["description"] = "静态图输入尺寸由导出产物固定。"
    return AlgorithmManifest.model_validate({
        "schema_version": "1.1", "id": key, "name": name or key, "version": version,
        "description": description, "task_type": "object_detection",
        "runtime": {"framework": "onnx", "device": "cpu", "min_memory_mb": 1024},
        "input": {"media_types": ["image/jpeg", "image/png"]},
        "output": {"type": "object_detection"}, "parameters": parameters,
    })


def publish_export(container, project_id, actor, job_id, payload, image):
    run = container.training.get_run(job_id)
    if (run is None or run.project_id != project_id
        or (run.progress or {}).get("operation") != "export"):
        raise ApplicationError("EXPORT_NOT_FOUND", "导出任务不存在", 404)
    if run.status is not TrainingStatus.COMPLETED:
        raise ApplicationError("EXPORT_NOT_VERIFIED", "仅验证成功的导出任务可以发布", 409)
    root = container.training_storage.run_directory(job_id)
    try:
        entries = registered_artifacts(root, verify_hash=True)
        if "export-report.json" not in entries:
            raise ValueError("缺少登记的导出报告")
        report = json.loads((root / "export-report.json").read_text(encoding="utf-8"))
        artifact = report.get("artifact")
        if (report.get("format") != "onnx" or report.get("precision") != "fp32"
            or report.get("quality_gate", {}).get("passed") is not True
            or report.get("smoke_test", {}).get("passed") is not True
            or artifact != "model/model.onnx"
            or artifact not in entries
            or entries[artifact]["sha256"] != report.get("artifact_sha256")):
            raise ValueError("导出精度、冒烟、格式或主模型校验失败")
        if not KEY.fullmatch(payload.key):
            raise ValueError("模型 ID 需要 3～64 位小写字母、数字和连字符")
        family = [a for a in container.algorithms.list()
                  if a.project_id == project_id and a.manifest.id == payload.key]
        if any(a.lineage is None for a in family):
            raise ValueError("模型 ID 已被导入的算法使用")
        version = payload.version.strip() or next_version(a.manifest.version for a in family)
        manifest = onnx_manifest(payload.key, payload.name.strip(), version,
                                 payload.description.strip() or f"ONNX 导出：{run.name}", report)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise ApplicationError("EXPORT_PUBLISH_INVALID", str(exc), 409) from exc
    if any(a.manifest.version == version for a in family):
        raise ApplicationError("ALGORITHM_VERSION_EXISTS", "模型版本已存在", 409)
    version_id = uuid4()
    # Copy the complete verified tree including external tensors and evidence.
    # Keep model/ paths intact: ONNX external tensor paths are graph-relative.
    try:
        files = container.model_store.publish_export(
            version_id, root, entries,
            yaml.safe_dump(manifest.model_dump(mode="json"), allow_unicode=True, sort_keys=False))
        algorithm = replace(AlgorithmVersion.available(
            manifest, image, algorithm_id=version_id, project_id=project_id, created_by=actor),
            model_directory=f"trained/{version_id.hex}", lineage={
                "export_job_id": str(job_id), "export_name": run.name,
                "training_run_id": (run.progress or {}).get("source", {}).get("training_run_id"),
                "dataset_id": str(run.dataset_id), "dataset": (run.progress or {}).get("dataset"),
                "class_names": report.get("class_names", []), "export_report": report,
                "files": files,
            })
        container.algorithms.add(algorithm)
    except Exception:
        container.model_store.delete(version_id)
        raise
    return algorithm
