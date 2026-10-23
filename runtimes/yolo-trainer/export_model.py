"""Offline FP32 ONNX conversion, same-data validation and CPU microbenchmark."""

import json
import os
import platform
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

import yaml
from cv_algorithm_sdk.export_artifacts import digest, write_manifest
from cv_algorithm_sdk.exports import EXPORT_PROTOCOL, ExportVerification, verification_parameters
from cv_algorithm_sdk.runtime_contract import FRAMEWORK_VERSION, normalize_parameters


@contextmanager
def cpu_sessions(ort, threads):
    """Pinned upstream backend creates its own ORT sessions; apply the job's limits."""
    original = ort.InferenceSession

    def create(path, sess_options=None, *args, **kwargs):
        options = sess_options or ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        return original(path, options, *args, **kwargs)

    ort.InferenceSession = create
    try:
        yield
    finally:
        ort.InferenceSession = original


def run_export(config, **paths):
    import onnxruntime as ort

    policy = ExportVerification.model_validate(config.get("export_verification") or {})
    with cpu_sessions(ort, policy.threads):
        _run_export(config, **paths)


def _run_export(config, *, workspace: Path, weights: Path, input_weight: Path, data: Path):
    import numpy as np
    import onnx
    import onnxruntime as ort
    import torch
    import ultralytics
    from ultralytics import YOLO

    if ultralytics.__version__ != FRAMEWORK_VERSION:
        raise ValueError("导出框架版本与契约不匹配")
    os.environ["YOLO_AUTOINSTALL"] = "false"
    os.environ.setdefault("YOLO_CONFIG_DIR", str(workspace / ".config"))
    config_dir = Path(os.environ["YOLO_CONFIG_DIR"])
    config_dir.mkdir(parents=True, exist_ok=True)
    for font in Path("/opt/ultralytics-assets").glob("*.ttf"):
        shutil.copyfile(font, config_dir / font.name)
    control = {
        "model",
        "operation",
        "dataset",
        "dataset_sha256",
        "training_input",
        "device",
        "output",
        "parameter_contract_id",
        "submitted_parameters",
        "export_verification",
    }
    parameters = normalize_parameters(
        "export", {k: v for k, v in config.items() if k not in control}
    )
    policy = ExportVerification.model_validate(config.get("export_verification") or {})
    validation = verification_parameters(parameters, policy)
    torch.set_num_threads(policy.threads)
    source = config.get("training_input")
    original = input_weight if source else weights / config["model"]
    original_sha = digest(original)
    if source and (source.get("resume") or source["sha256"] != original_sha):
        raise ValueError("输入权重恢复模式或 SHA256 不正确")
    output = workspace / "train"
    output.mkdir(exist_ok=True)

    def save(path, value):
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, allow_nan=False, default=str), encoding="utf-8"
        )
        temporary.replace(path)

    def phase(value):
        print(f"export phase: {value}", flush=True)
        save(workspace / "progress.json", {"phase": value})

    model = YOLO(str(original))
    declared = yaml.safe_load(data.read_text(encoding="utf-8"))["names"]
    names = list(declared.values()) if isinstance(declared, dict) else declared
    if model.task != "detect" or [model.names[i] for i in range(len(model.names))] != names:
        raise ValueError("导出验证需要目标检测模型，数据集类别名称及顺序须与模型一致")
    # Exporter writes beside the checkpoint, so copy into this task's writable area.
    work = workspace / "export-work"
    work.mkdir(exist_ok=True)
    local = work / "model.pt"
    shutil.copyfile(original, local)
    phase("converting")
    exporter = YOLO(str(local))
    effective = {}
    exporter.add_callback("on_export_end", lambda state: effective.update(vars(state.args)))
    converted = Path(exporter.export(**parameters, device="cpu", nms=False))
    if not converted.is_file() or converted.parent.resolve() != work.resolve():
        raise ValueError("转换器未返回本任务目录内的 ONNX 文件")
    model_dir = output / "model"
    model_dir.mkdir(exist_ok=True)
    for path in work.iterdir():
        if path != local and path.is_file():
            shutil.move(str(path), str(model_dir / path.name))
    graph_path = model_dir / converted.name
    graph = onnx.load(graph_path)
    onnx.checker.check_model(graph, full_check=True)
    actual_opset = next(item.version for item in graph.opset_import if item.domain == "")
    options = ort.SessionOptions()
    options.intra_op_num_threads = policy.threads
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(graph_path), sess_options=options, providers=["CPUExecutionProvider"]
    )
    inputs = session.get_inputs()
    if len(inputs) != 1 or inputs[0].type != "tensor(float)":
        raise ValueError("导出结果不是单输入 FP32 图")
    requested = parameters["imgsz"]
    h, w = (requested, requested) if isinstance(requested, int) else requested
    shape = [
        int(d) if isinstance(d, int) else fallback
        for d, fallback in zip(inputs[0].shape, [parameters["batch"], 3, h, w], strict=True)
    ]
    phase("smoke_test")
    smoke = session.run(None, {inputs[0].name: np.zeros(shape, dtype=np.float32)})
    if not smoke or not all(np.isfinite(item).all() for item in smoke):
        raise ValueError("ONNX Runtime 冒烟推理未返回有限数值")
    metadata = {item.key: item.value for item in graph.metadata_props}
    del graph, session, smoke
    arguments = {
        **validation,
        "data": str(data),
        "device": "cpu",
        "project": str(output),
        "exist_ok": True,
    }

    def validate(path, name):
        phase(f"validating_{name}")
        current = YOLO(str(path), task="detect")
        effective_val = {}
        current.add_callback(
            "on_val_start", lambda validator: effective_val.update(vars(validator.args))
        )
        result = current.val(**arguments, name=name)
        metrics = {key: float(value) for key, value in result.results_dict.items()}
        per_class = [
            {
                "class_id": int(cls),
                "name": result.names[int(cls)],
                "instances": int(result.nt_per_class[int(cls)]),
                "precision": float(result.box.p[i]),
                "recall": float(result.box.r[i]),
                "map50": float(result.box.ap50[i]),
                "map50_95": float(result.box.ap[i]),
            }
            for i, cls in enumerate(result.box.ap_class_index)
        ]
        save(
            output / f"{name}-evaluation.json",
            {
                "metrics": metrics,
                "per_class": per_class,
                "effective": effective_val,
                "speed_ms_per_image": result.speed,
            },
        )
        return metrics, effective_val

    baseline, baseline_effective = validate(original, "reference")
    exported, exported_effective = validate(graph_path, "onnx")
    image_dir = Path(config["dataset"]) / "images" / validation["split"]
    sample = next(
        (
            p
            for p in sorted(image_dir.rglob("*"))
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        ),
        None,
    )
    if sample is None:
        raise ValueError("验证划分没有可用的冒烟图片")
    phase("benchmarking")

    def benchmark(path):
        current = YOLO(str(path), task="detect")
        samples = [str(sample)] * parameters["batch"]
        values = []
        for i in range(policy.warmup + policy.iterations):
            start = time.perf_counter()
            current.predict(
                samples,
                device="cpu",
                imgsz=parameters["imgsz"],
                batch=parameters["batch"],
                rect=False,
                verbose=False,
                save=False,
                conf=validation["conf"],
                iou=validation["iou"],
                **{
                    key: validation[key]
                    for key in ("max_det", "classes", "agnostic_nms", "nms")
                    if key in validation
                },
            )
            elapsed = (time.perf_counter() - start) * 1000 / len(samples)
            if i >= policy.warmup:
                values.append(elapsed)
        return {
            "mean_ms_per_image": float(np.mean(values)),
            "p50_ms_per_image": float(np.percentile(values, 50)),
            "p95_ms_per_image": float(np.percentile(values, 95)),
            "images_per_second": 1000 / float(np.mean(values)),
            "iterations": policy.iterations,
            "warmup": policy.warmup,
            "batch": len(samples),
            "scope": "single-process CPU predict, repeated sample; includes pre/postprocessing",
        }

    benchmarks = {"reference": benchmark(original), "onnx": benchmark(graph_path)}
    differences = {key: exported[key] - baseline[key] for key in baseline if key in exported}
    drop = baseline["metrics/mAP50-95(B)"] - exported["metrics/mAP50-95(B)"]
    passed = drop <= policy.max_map_drop
    resolution = {
        "submitted": config.get("submitted_parameters"),
        "normalized": parameters,
        "effective": effective,
        "validation_requested": validation,
        "reference_effective": baseline_effective,
        "onnx_effective": exported_effective,
    }
    save(output / "parameter-resolution.json", resolution)
    report = {
        "protocol": EXPORT_PROTOCOL,
        "format": "onnx",
        "precision": "fp32",
        "source_sha256": original_sha,
        "artifact": "model/" + graph_path.name,
        "artifact_sha256": digest(graph_path),
        "dataset_sha256": config["dataset_sha256"],
        "split": validation["split"],
        "class_names": names,
        "input_shape": shape,
        "dynamic": parameters["dynamic"],
        "opset": actual_opset,
        "graph_metadata": metadata,
        "providers": ["CPUExecutionProvider"],
        "versions": {
            "ultralytics": ultralytics.__version__,
            "torch": torch.__version__,
            "onnx": onnx.__version__,
            "onnxruntime": ort.__version__,
            "python": platform.python_version(),
        },
        "hardware": {"processor": platform.processor(), "threads_requested": policy.threads},
        "reference_metrics": baseline,
        "onnx_metrics": exported,
        "metric_deltas": differences,
        "quality_gate": {
            "passed": passed,
            "map50_95_drop": drop,
            "max_map_drop": policy.max_map_drop,
        },
        "benchmark": benchmarks,
        "smoke_test": {"passed": True},
    }
    save(output / "export-report.json", report)
    registered = [
        p.relative_to(output).as_posix()
        for p in output.rglob("*")
        if p.is_file() and not p.name.startswith(".")
    ]
    write_manifest(output, registered)
    phase("completed" if passed else "quality_gate_failed")
    if not passed:
        raise ValueError(
            f"ONNX 精度下降 {drop:.6f} 超过允许值 {policy.max_map_drop:.6f}；报告已保留"
        )
