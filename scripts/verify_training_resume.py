"""Offline, isolated GPU regression. Run in the pinned trainer image with source mounted.

No platform API, database, node credentials or production volumes are used.
Example: python /tests/scripts/verify_training_resume.py /evidence
"""

import csv
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


def runner():
    entry = Path(__file__).resolve().parents[1] / "runtimes/yolo-trainer/train.py"
    spec = importlib.util.spec_from_file_location("platform_trainer", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def child(root: Path, phase: str):
    module = runner()
    module.WORKSPACE = root / phase
    module.WORKSPACE.mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = str(module.WORKSPACE / ".config")
    config = {
        "model": "yolo11n.pt",
        "device": "0",
        "dataset": str(root / "dataset"),
        "dataset_sha256": "a" * 64,
        "epochs": 3,
        "batch": 2,
        "imgsz": 64,
        "workers": 0,
        "amp": True,
        "plots": False,
        "optimizer": "SGD",
        "close_mosaic": 0,
        "mosaic": 0,
        "save": True,
        "seed": 17,
    }
    if phase == "interrupted":
        import torch
        import ultralytics

        original_yolo = ultralytics.YOLO

        class FixtureYOLO(original_yolo):
            def train(self, **kwargs):
                # Tiny smoke fixtures can overflow at the default 65536 scale before
                # any optimizer step. Start lower so the checkpoint exercises both
                # a non-empty momentum state and an enabled AMP scaler.
                self.add_callback(
                    "on_train_start",
                    lambda trainer: setattr(
                        trainer,
                        "scaler",
                        torch.amp.GradScaler("cuda", init_scale=128.0),
                    ),
                )
                return super().train(**kwargs)

        ultralytics.YOLO = FixtureYOLO
        original = module.preserve_checkpoint

        def interrupt(trainer, arguments):
            original(trainer, arguments)
            # Abrupt process death immediately after the first committed checkpoint.
            os._exit(137)

        module.preserve_checkpoint = interrupt
    else:
        metadata = json.loads((root / "interrupted/train/checkpoint.json").read_text())
        module.INPUT_WEIGHT = root / "interrupted/train" / metadata["file"]
        config["training_input"] = {
            "sha256": metadata["sha256"],
            "dataset_sha256": "a" * 64,
            "resume": phase == "resumed",
            "epoch": metadata["epoch"],
            "epochs": metadata["epochs"],
            "optimizer": metadata.get("optimizer"),
            "lr0": metadata.get("lr0"),
        }
        if phase == "resumed":
            module.INPUT_BEST = root / "interrupted/train/weights/best.pt"
            config["training_input"]["best_sha256"] = module.file_sha256(module.INPUT_BEST)
        if phase == "finetuned":
            config["epochs"] = 1
        if phase == "validated":
            module.INPUT_WEIGHT = root / "resumed/train/weights/last.pt"
            config = {
                "operation": "val",
                "model": "custom.pt",
                "device": "0",
                "dataset": str(root / "dataset"),
                "dataset_sha256": "a" * 64,
                "batch": 2,
                "imgsz": 64,
                "workers": 0,
                "split": "test",
                "plots": True,
                "training_input": {
                    "sha256": module.file_sha256(module.INPUT_WEIGHT),
                    "resume": False,
                },
            }
    (module.WORKSPACE / "config.json").write_text(json.dumps(config))
    raise SystemExit(module.main())


def verify(root: Path):
    import torch
    from PIL import Image, ImageDraw

    root.mkdir(parents=True, exist_ok=True)
    dataset = root / "dataset"
    for split, count in (("train", 8), ("val", 4), ("test", 4)):
        for index in range(count):
            image = dataset / f"images/{split}/{index}.png"
            label = dataset / f"labels/{split}/{index}.txt"
            image.parent.mkdir(parents=True, exist_ok=True)
            label.parent.mkdir(parents=True, exist_ok=True)
            canvas = Image.new("RGB", (64, 64), (index * 20, 20, 30))
            ImageDraw.Draw(canvas).rectangle(
                (16, 16, 48, 48), fill=(220, index * 15, 100)
            )
            canvas.save(image)
            label.write_text("0 0.5 0.5 0.5 0.5\n")
    (dataset / "data.yaml").write_text("names: [box]\n")
    for phase, expected in (
        ("interrupted", 137),
        ("resumed", 0),
        ("finetuned", 0),
        ("validated", 0),
    ):
        with (root / f"{phase}.log").open("w") as output:
            result = subprocess.run(
                [sys.executable, __file__, str(root), phase],
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if result.returncode != expected:
            print((root / f"{phase}.log").read_text()[-12000:])
            raise AssertionError(
                f"{phase}: exit={result.returncode}, expected={expected}"
            )
        print(f"{phase}: verified exit {result.returncode}", flush=True)
    metadata = json.loads((root / "interrupted/train/checkpoint.json").read_text())
    original = torch.load(
        root / "interrupted/train" / metadata["file"],
        map_location="cpu",
        weights_only=False,
    )
    restored = json.loads((root / "resumed/train/resume-restoration.json").read_text())
    assert restored["start_epoch"] == 1 and restored["next_epoch"] == 2
    assert restored["optimizer_states"] == len(original["optimizer"]["state"]) > 0
    assert restored["ema_updates"] == original["updates"] > 0
    assert restored["scaler"] == original["scaler"]
    assert restored["scaler"]["scale"] > 0
    with (root / "resumed/train/results.csv").open() as stream:
        rows = [
            {key.strip(): value for key, value in row.items()}
            for row in csv.DictReader(stream)
        ]
    assert [int(float(row["epoch"])) for row in rows] == [1, 2, 3]
    stripped = torch.load(
        root / "resumed/train/weights/last.pt", map_location="cpu", weights_only=False
    )
    assert stripped["epoch"] == -1 and stripped["optimizer"] is None
    final = json.loads((root / "resumed/train/checkpoint.json").read_text())
    full = torch.load(
        root / "resumed/train" / final["file"], map_location="cpu", weights_only=False
    )
    assert full["epoch"] == 2 and full["optimizer"] is not None
    assert not final["resumable"]
    assert (root / "resumed/train/weights/best.pt").is_file()
    assert not (root / "finetuned/train/resume-restoration.json").exists()
    evaluation = json.loads((root / "validated/train/evaluation.json").read_text())
    assert evaluation["split"] == "test" and len(evaluation["per_class"]) == 1
    assert evaluation["per_class"][0]["instances"] == 4
    assert evaluation["confusion_matrix"] is not None
    assert not (root / "validated/train/weights").exists()
    with (root / "finetuned/train/results.csv").open() as stream:
        assert len(list(csv.DictReader(stream))) == 1
    summary = {
        "gpu": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "checkpoint": metadata,
        "restoration": restored,
        "resumed_epochs": [1, 2, 3],
        "finetune_epochs": [1],
        "last_optimizer_stripped": True,
        "full_optimizer_preserved": True,
        "evaluation": evaluation,
    }
    (root / "VERIFIED.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3:
        child(Path(sys.argv[1]), sys.argv[2])
    else:
        verify(Path(sys.argv[1]))
