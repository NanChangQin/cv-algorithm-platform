"""平台训练容器入口：按 /workspace/config.json 训练 YOLO 目标检测模型。

进度写入 /workspace/progress.json，结束状态写入 /workspace/status.json，
Ultralytics 的训练产物（权重、results.csv、曲线图）在 /workspace/train。
"""

import json
import os
import shutil
import time
import traceback
from pathlib import Path

import yaml

WORKSPACE = Path(os.environ.get("CV_TRAINING_WORKSPACE", "/workspace"))
WEIGHTS = Path(os.environ.get("CV_TRAINING_WEIGHTS", "/opt/weights"))
ASSETS = Path("/opt/ultralytics-assets")


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def friendly_error(exc: BaseException) -> str:
    text = str(exc)
    if "out of memory" in text.lower():
        return "显存不足，请减小 batch 或图片尺寸后重试"
    return f"{type(exc).__name__}: {text}"[:2000]


def prepare_data(dataset: Path) -> Path:
    source = yaml.safe_load((dataset / "data.yaml").read_text(encoding="utf-8"))
    data = {
        "path": str(dataset),
        "train": "images/train",
        "val": "images/val",
        "names": source["names"],
    }
    target = WORKSPACE / "data.yaml"
    target.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return target


def train(config: dict) -> None:
    config_dir = Path(os.environ.get("YOLO_CONFIG_DIR", WORKSPACE / ".config"))
    config_dir.mkdir(parents=True, exist_ok=True)
    # 断网环境下绘制曲线图需要的字体
    for font in ASSETS.glob("*.ttf"):
        shutil.copy(font, config_dir / font.name)
    data = prepare_data(Path(config["dataset"]))
    weights = WEIGHTS / config["model"]
    if not weights.is_file():
        raise FileNotFoundError(f"训练镜像中没有预训练权重 {config['model']}")

    from ultralytics import YOLO

    started_at = time.time()
    state = {"batch": 0, "written_at": 0.0}

    def report(trainer, phase: str, **extra) -> None:
        write_json(WORKSPACE / "progress.json", {
            "phase": phase,
            "epoch": trainer.epoch + 1,
            "epochs": trainer.epochs,
            "started_at": started_at,
            "updated_at": time.time(),
            **extra,
        })

    def on_train_epoch_start(trainer) -> None:
        state["batch"] = 0
        report(trainer, "train", batch=0, batches=len(trainer.train_loader))

    def on_train_batch_end(trainer) -> None:
        state["batch"] += 1
        now = time.time()
        if now - state["written_at"] < 2:
            return
        state["written_at"] = now
        losses = trainer.label_loss_items(trainer.tloss, prefix="train")
        report(
            trainer, "train",
            batch=state["batch"], batches=len(trainer.train_loader),
            losses={key: round(float(value), 5) for key, value in losses.items()},
        )

    def on_train_epoch_end(trainer) -> None:
        report(trainer, "val", batch=state["batch"], batches=len(trainer.train_loader))

    def on_fit_epoch_end(trainer) -> None:
        report(trainer, "epoch_end", batch=state["batch"], batches=len(trainer.train_loader))

    model = YOLO(str(weights))
    model.add_callback("on_train_epoch_start", on_train_epoch_start)
    model.add_callback("on_train_batch_end", on_train_batch_end)
    model.add_callback("on_train_epoch_end", on_train_epoch_end)
    model.add_callback("on_fit_epoch_end", on_fit_epoch_end)
    write_json(WORKSPACE / "progress.json", {
        "phase": "preparing", "epoch": 0, "epochs": config["epochs"],
        "started_at": started_at, "updated_at": time.time(),
    })
    model.train(
        data=str(data),
        epochs=config["epochs"],
        batch=config["batch"],
        imgsz=config["imgsz"],
        lr0=config["lr0"],
        optimizer=config["optimizer"],
        patience=config["patience"],
        workers=config["workers"],
        seed=config["seed"],
        cos_lr=config["cos_lr"],
        device=config["device"],
        project=str(WORKSPACE),
        name="train",
        exist_ok=True,
        plots=True,
    )


def main() -> int:
    try:
        config = json.loads((WORKSPACE / "config.json").read_text(encoding="utf-8"))
        train(config)
    except BaseException as exc:
        traceback.print_exc()
        write_json(WORKSPACE / "status.json", {
            "state": "failed", "error": friendly_error(exc), "finished_at": time.time(),
        })
        return 1
    write_json(WORKSPACE / "status.json", {"state": "completed", "finished_at": time.time()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
