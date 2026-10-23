"""Isolated ONNX regression against an existing fixture weight; no platform credentials."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from cv_algorithm_sdk.export_artifacts import digest, registered_artifacts


def child(root, original, case):
    entry = Path(__file__).resolve().parents[1] / "runtimes/yolo-trainer/train.py"
    spec = importlib.util.spec_from_file_location("platform_trainer", entry)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.WORKSPACE = root / case
    runner.WORKSPACE.mkdir(parents=True)
    runner.INPUT_WEIGHT = original
    os.environ["YOLO_CONFIG_DIR"] = str(runner.WORKSPACE / ".config")
    config = {
        "operation": "export",
        "model": "custom.pt",
        "dataset": str(root / "dataset"),
        "dataset_sha256": digest(root / "dataset/data.yaml"),
        "device": "cpu",
        "training_input": {"sha256": digest(original), "resume": False},
        "format": "onnx",
        "imgsz": [64, 96] if case == "dynamic" else 64,
        "batch": 2 if case == "dynamic" else 1,
        "dynamic": case == "dynamic",
        "simplify": True,
        "opset": 17,
        "export_verification": {
            "parameters": {"split": "test"},
            "warmup": 1,
            "iterations": 3,
            "threads": 2,
            "max_map_drop": 0.01,
        },
    }
    (runner.WORKSPACE / "config.json").write_text(json.dumps(config))
    raise SystemExit(runner.main())


def verify(root, original):
    from PIL import Image, ImageDraw

    root.mkdir(parents=True, exist_ok=True)
    for split in ("train", "val", "test"):
        for index in range(4):
            image = root / f"dataset/images/{split}/{index}.png"
            label = root / f"dataset/labels/{split}/{index}.txt"
            image.parent.mkdir(parents=True, exist_ok=True)
            label.parent.mkdir(parents=True, exist_ok=True)
            canvas = Image.new("RGB", (64, 64), (index * 20, 20, 30))
            ImageDraw.Draw(canvas).rectangle((16, 16, 48, 48), fill=(220, index * 15, 100))
            canvas.save(image)
            label.write_text("0 0.5 0.5 0.5 0.5\n")
    (root / "dataset/data.yaml").write_text("names: [box]\n")
    results = {}
    for case in ("static", "dynamic"):
        with (root / f"{case}.log").open("w") as stream:
            result = subprocess.run(
                [sys.executable, __file__, str(root), str(original), case],
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if result.returncode:
            print((root / f"{case}.log").read_text()[-14000:])
            raise AssertionError(f"{case} exited {result.returncode}")
        output = root / case / "train"
        entries = registered_artifacts(output, verify_hash=True)
        report = json.loads((output / "export-report.json").read_text())
        assert report["quality_gate"]["passed"] and report["smoke_test"]["passed"]
        assert report["opset"] == 17 and report["source_sha256"] == digest(original)
        assert report["dynamic"] == (case == "dynamic")
        assert report["input_shape"] == ([2, 3, 64, 96] if case == "dynamic" else [1, 3, 64, 64])
        assert report["artifact"] in entries and not any(name.endswith(".pt") for name in entries)
        assert report["benchmark"]["onnx"]["iterations"] == 3
        results[case] = report
        print(
            f"{case}: export, ORT smoke, same-dataset validation and benchmark passed", flush=True
        )
    (root / "VERIFIED.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    if len(sys.argv) == 4:
        child(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    else:
        verify(Path(sys.argv[1]), Path(sys.argv[2]))
