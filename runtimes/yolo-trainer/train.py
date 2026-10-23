"""平台训练容器入口：按 /workspace/config.json 训练 YOLO 目标检测模型。

进度写入 /workspace/progress.json，结束状态写入 /workspace/status.json，
Ultralytics 的训练产物（权重、results.csv、曲线图）在 /workspace/train。
"""

import csv
import hashlib
import json
import math
import os
import shutil
import time
import traceback
from pathlib import Path

import yaml
from cv_algorithm_sdk.runtime_contract import (
    CONTRACT_ID,
    FRAMEWORK_VERSION,
    normalize_parameters,
    parameter_warnings,
)
from cv_algorithm_sdk.training_sources import TRAINING_PROTOCOL

WORKSPACE = Path(os.environ.get("CV_TRAINING_WORKSPACE", "/workspace"))
WEIGHTS = Path(os.environ.get("CV_TRAINING_WEIGHTS", "/opt/weights"))
ASSETS = Path("/opt/ultralytics-assets")
INPUT_WEIGHT = Path("/input/model.pt")
INPUT_BEST = Path("/input/best.pt")


def snapshot_value(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [snapshot_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): snapshot_value(item) for key, item in value.items()}
    return str(value)


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(snapshot_value(value), ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
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
    if (dataset / "images/test").is_dir():
        data["test"] = "images/test"
    target = WORKSPACE / "data.yaml"
    target.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return target


def training_arguments(config: dict, data: Path) -> dict:
    """Validate every supplied argument, then map only platform-owned fields."""
    if config.get("parameter_contract_id", CONTRACT_ID) != CONTRACT_ID:
        raise ValueError("训练参数契约不匹配，请更新训练镜像")
    control = {
        "model",
        "dataset",
        "output",
        "device",
        "parameter_contract_id",
        "submitted_parameters",
        "training_input",
        "dataset_sha256",
        "operation",
    }
    parameters = normalize_parameters(
        "train", {key: value for key, value in config.items() if key not in control}
    )
    return {
        **parameters,
        "data": str(data),
        "device": config["device"],
        "project": str(WORKSPACE),
        "name": "train",
        "exist_ok": True,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def preserve_checkpoint(trainer, config: dict) -> None:
    """Preserve the full upstream checkpoint before final_eval strips optimizer/epoch."""
    epoch = trainer.epoch + 1
    target = WORKSPACE / "train" / "weights" / f"resume-{epoch:06d}.pt"
    temporary = target.with_name(".resume.tmp")
    shutil.copyfile(trainer.last, temporary)
    digest = file_sha256(temporary)
    temporary.replace(target)
    resumable = epoch < trainer.epochs and not bool(trainer.args.time)
    write_json(
        WORKSPACE / "train" / "checkpoint.json",
        {
            "protocol": TRAINING_PROTOCOL,
            "framework_version": FRAMEWORK_VERSION,
            "sha256": digest,
            "epoch": epoch,
            "epochs": trainer.epochs,
            "file": f"weights/{target.name}",
            "dataset_sha256": config.get("dataset_sha256"),
            "resumable": resumable,
            "reason": (
                None
                if resumable
                else "按时间训练请使用权重微调"
                if trainer.args.time
                else "已达到计划总轮数，请使用权重微调开始新实验"
            ),
            "state": ["epoch", "optimizer", "scaler", "ema", "updates", "stopper"],
            "optimizer": type(trainer.optimizer).__name__
            if hasattr(trainer, "optimizer")
            else None,
            "lr0": trainer.optimizer.param_groups[0].get(
                "initial_lr", trainer.optimizer.param_groups[0]["lr"]
            )
            if hasattr(trainer, "optimizer")
            else None,
        },
    )
    # Metadata commits the new generation before the previous one is removed.
    # A kill during copy leaves the previous epoch and its metadata consistent.
    for previous in target.parent.glob("resume-*.pt"):
        if previous != target:
            previous.unlink(missing_ok=True)


def resume_trainer(checkpoint: dict, arguments: dict, source: dict):
    # Loaded only in the isolated training container, never in the control plane.
    from ultralytics.models.yolo.detect import DetectionTrainer

    class PlatformResumeTrainer(DetectionTrainer):
        def check_resume(self, overrides):
            super().check_resume(overrides)
            # The checkpoint's original host paths never control this run's outputs/data.
            self.args.project = str(WORKSPACE)
            self.args.name = "train"
            self.args.exist_ok = True
            self.args.save_dir = str(WORKSPACE / "train")
            self.args.data = arguments["data"]
            if source.get("optimizer"):
                # Auto optimizer selection can change when batch/iterations change.
                # Rebuild the original optimizer and scheduler before loading state.
                self.args.optimizer = source["optimizer"]
                self.args.lr0 = source["lr0"]
            if self.args.epochs != source["epochs"]:
                raise ValueError("checkpoint 计划轮数不一致，不能续训")

        def resume_training(self, ckpt):
            super().resume_training(ckpt)
            if self.start_epoch != source["epoch"]:
                raise ValueError("checkpoint 轮次恢复失败")
            actual = self.optimizer.state_dict()
            if len(actual["state"]) != len(checkpoint["optimizer"]["state"]):
                raise ValueError("optimizer 状态恢复失败")
            if self.ema.updates != checkpoint["updates"]:
                raise ValueError("EMA 状态恢复失败")
            if self.scaler.state_dict() != checkpoint["scaler"]:
                raise ValueError("AMP scaler 状态恢复失败")
            write_json(
                WORKSPACE / "train" / "resume-restoration.json",
                {
                    "start_epoch": self.start_epoch,
                    "next_epoch": self.start_epoch + 1,
                    "optimizer_states": len(actual["state"]),
                    "ema_updates": self.ema.updates,
                    "scaler": self.scaler.state_dict(),
                    "stopper": vars(self.stopper),
                    "source_sha256": source["sha256"],
                },
            )

    return PlatformResumeTrainer


def train(config: dict) -> None:
    config_dir = Path(os.environ.get("YOLO_CONFIG_DIR", WORKSPACE / ".config"))
    config_dir.mkdir(parents=True, exist_ok=True)
    # 断网环境下绘制曲线图需要的字体
    for font in ASSETS.glob("*.ttf"):
        shutil.copy(font, config_dir / font.name)
    dataset = Path(config["dataset"])
    # disk cache writes beside images; retain the shared dataset's read-only mount.
    if config.get("cache") == "disk":
        cached = WORKSPACE / "cached-dataset"
        shutil.copytree(dataset, cached, dirs_exist_ok=True)
        dataset = cached
    data = prepare_data(dataset)
    arguments = training_arguments(config, data)
    source = config.get("training_input")
    weights = INPUT_WEIGHT if source else WEIGHTS / config["model"]
    if not weights.is_file():
        raise FileNotFoundError(f"训练镜像中没有预训练权重 {config['model']}")
    if source and file_sha256(weights) != source["sha256"]:
        raise ValueError("输入权重 SHA256 校验失败")

    import ultralytics
    import ultralytics.utils
    from ultralytics import YOLO

    if ultralytics.__version__ != FRAMEWORK_VERSION:
        raise ValueError(
            f"训练镜像 Ultralytics {ultralytics.__version__} 与参数契约 {FRAMEWORK_VERSION} 不匹配"
        )

    # 默认权重目录是相对当前目录的 weights/：AMP 自检找不到 yolo26n.pt 就会尝试联网下载。
    # 指向镜像里打包好的权重目录，断网也能完成自检。
    ultralytics.utils.SETTINGS.update(weights_dir=str(WEIGHTS))
    ultralytics.utils.WEIGHTS_DIR = WEIGHTS

    started_at = time.time()
    state = {"batch": 0, "written_at": 0.0}
    parameters = {
        key: value
        for key, value in arguments.items()
        if key not in {"data", "device", "project", "name", "exist_ok"}
    }
    resolution = {
        "contract_id": CONTRACT_ID,
        "framework_version": FRAMEWORK_VERSION,
        "submitted": config["submitted_parameters"]
        if config.get("submitted_parameters") is not None
        else parameters,
        "normalized": arguments,
        "warnings": parameter_warnings(parameters),
    }

    def save_resolution(trainer=None) -> None:
        if trainer is not None:
            effective = {
                key: value
                for key, value in vars(trainer.args).items()
                if value is None
                or isinstance(value, (str, bool, int, float, list, dict))
            }
            effective["batch"] = getattr(trainer, "batch_size", effective.get("batch"))
            optimizer = getattr(trainer, "optimizer", None)
            if optimizer is not None:
                effective["optimizer"] = type(optimizer).__name__
                effective["learning_rates"] = [
                    group["lr"] for group in optimizer.param_groups
                ]
                if optimizer.param_groups:
                    effective["lr0"] = optimizer.param_groups[0].get(
                        "initial_lr", optimizer.param_groups[0]["lr"]
                    )
            resolution["effective"] = effective
            resolution["adjustments"] = {
                key: {"requested": value, "effective": effective[key]}
                for key, value in arguments.items()
                if key in effective and value != effective[key]
            }
        write_json(WORKSPACE / "parameter-resolution.json", resolution)
        output = WORKSPACE / "train"
        output.mkdir(exist_ok=True)
        write_json(output / "parameter-resolution.json", resolution)

    save_resolution()

    def report(trainer, phase: str, **extra) -> None:
        write_json(
            WORKSPACE / "progress.json",
            {
                "phase": phase,
                # 训练结束后的最终验证也会触发回调，此时 epoch 已等于总轮数
                "epoch": min(trainer.epoch + 1, trainer.epochs),
                "epochs": trainer.epochs,
                "started_at": started_at,
                "updated_at": time.time(),
                **extra,
            },
        )

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
            trainer,
            "train",
            batch=state["batch"],
            batches=len(trainer.train_loader),
            losses={key: round(float(value), 5) for key, value in losses.items()},
        )

    def on_train_epoch_end(trainer) -> None:
        report(trainer, "val", batch=state["batch"], batches=len(trainer.train_loader))

    def on_fit_epoch_end(trainer) -> None:
        save_resolution(trainer)
        report(
            trainer,
            "epoch_end",
            batch=state["batch"],
            batches=len(trainer.train_loader),
        )

    model = YOLO(str(weights))
    if getattr(model, "task", "detect") != "detect":
        raise ValueError("当前训练入口只支持 Ultralytics 目标检测 .pt 权重")
    trainer_options = {}
    if source and source.get("resume"):
        checkpoint = model.ckpt
        required = {"epoch", "optimizer", "scaler", "ema", "updates", "stopper"}
        if (
            not required <= checkpoint.keys()
            or checkpoint["optimizer"] is None
            or checkpoint["ema"] is None
            or checkpoint.get("version") != FRAMEWORK_VERSION
        ):
            raise ValueError("权重缺少完整恢复状态或框架版本不匹配，请使用微调")
        if (
            checkpoint["epoch"] + 1 != source["epoch"]
            or not 0 < source["epoch"] < source["epochs"]
        ):
            raise ValueError("checkpoint 已完成训练或轮次不匹配，请使用微调")
        if checkpoint.get("train_results"):
            history = checkpoint["train_results"]
            with (WORKSPACE / "train" / "results.csv").open(
                "w", newline="", encoding="utf-8"
            ) as output:
                writer = csv.writer(output)
                writer.writerow(history)
                writer.writerows(zip(*history.values(), strict=True))
        arguments["resume"] = str(weights)
        if source.get("best_sha256"):
            if file_sha256(INPUT_BEST) != source["best_sha256"]:
                raise ValueError("原最佳权重 SHA256 校验失败")
            best = WORKSPACE / "train" / "weights" / "best.pt"
            best.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(INPUT_BEST, best)
        trainer_options["trainer"] = resume_trainer(checkpoint, arguments, source)
    if arguments.get("pretrained") is False and not (source and source.get("resume")):
        architecture = WORKSPACE / "model.yaml"
        architecture.write_text(yaml.safe_dump(model.model.yaml), encoding="utf-8")
        model = YOLO(str(architecture), task="detect")
    model.add_callback("on_train_start", save_resolution)
    model.add_callback("on_train_epoch_start", on_train_epoch_start)
    model.add_callback("on_train_batch_end", on_train_batch_end)
    model.add_callback("on_train_epoch_end", on_train_epoch_end)
    model.add_callback("on_fit_epoch_end", on_fit_epoch_end)
    model.add_callback(
        "on_model_save", lambda trainer: preserve_checkpoint(trainer, config)
    )
    write_json(
        WORKSPACE / "progress.json",
        {
            "phase": "preparing",
            "epoch": 0,
            "epochs": config["epochs"],
            "started_at": started_at,
            "updated_at": time.time(),
        },
    )
    model.train(**arguments, **trainer_options)


def evaluate(config: dict) -> None:
    """Independent Ultralytics val(), never enters the training/optimizer path."""
    import ultralytics
    from ultralytics import YOLO

    if (
        ultralytics.__version__ != FRAMEWORK_VERSION
        or config.get("parameter_contract_id", CONTRACT_ID) != CONTRACT_ID
    ):
        raise ValueError("验证镜像与参数契约不匹配")
    config_dir = Path(os.environ.get("YOLO_CONFIG_DIR", WORKSPACE / ".config"))
    config_dir.mkdir(parents=True, exist_ok=True)
    for font in ASSETS.glob("*.ttf"):
        shutil.copy(font, config_dir / font.name)
    control = {
        "model",
        "dataset",
        "output",
        "device",
        "parameter_contract_id",
        "submitted_parameters",
        "training_input",
        "dataset_sha256",
        "operation",
    }
    parameters = normalize_parameters(
        "val", {key: value for key, value in config.items() if key not in control}
    )
    source = config.get("training_input")
    if source and source.get("resume"):
        raise ValueError("独立验证不支持 resume")
    weights = INPUT_WEIGHT if source else WEIGHTS / config["model"]
    if source and file_sha256(weights) != source["sha256"]:
        raise ValueError("输入权重 SHA256 校验失败")
    data = prepare_data(Path(config["dataset"]))
    arguments = {
        **parameters,
        "data": str(data),
        "device": config["device"],
        "project": str(WORKSPACE),
        "name": "train",
        "exist_ok": True,
    }
    output = WORKSPACE / "train"
    output.mkdir(exist_ok=True)
    model = YOLO(str(weights))
    if model.task != "detect":
        raise ValueError("独立验证当前只支持目标检测 .pt 模型")
    declared = yaml.safe_load(data.read_text(encoding="utf-8"))["names"]
    names = list(declared.values()) if isinstance(declared, dict) else declared
    if (
        not parameters.get("single_cls")
        and [model.names[index] for index in range(len(model.names))] != names
    ):
        raise ValueError(
            "数据集与模型的类别名称或顺序不同，请选择相同类别映射的数据版本"
        )
    batches = {"count": 0}
    effective = {}

    def capture(validator):
        effective.update(vars(validator.args))

    def progress(validator):
        batches["count"] += 1
        write_json(
            WORKSPACE / "progress.json",
            {
                "phase": "val",
                "epoch": 1,
                "epochs": 1,
                "batch": batches["count"],
                "batches": len(validator.dataloader),
            },
        )

    model.add_callback("on_val_start", capture)
    model.add_callback("on_val_batch_end", progress)
    result = model.val(**arguments)
    metrics = {key: float(value) for key, value in result.results_dict.items()}
    per_class = []
    for index, class_id in enumerate(result.box.ap_class_index):
        class_id = int(class_id)
        per_class.append(
            {
                "class_id": class_id,
                "name": result.names[class_id],
                "instances": int(result.nt_per_class[class_id]),
                "precision": float(result.box.p[index]),
                "recall": float(result.box.r[index]),
                "f1": float(result.box.f1[index]),
                "map50": float(result.box.ap50[index]),
                "map50_95": float(result.box.ap[index]),
            }
        )
    confusion = getattr(result, "confusion_matrix", None)
    report = {
        "framework_version": FRAMEWORK_VERSION,
        "split": parameters.get("split", "val"),
        "dataset_sha256": config.get("dataset_sha256"),
        "weight_sha256": file_sha256(weights),
        "metrics": metrics,
        "per_class": per_class,
        "speed": {key: float(value) for key, value in result.speed.items()},
        "confusion_matrix": confusion.matrix.tolist()
        if confusion is not None and effective.get("plots")
        else None,
        "confusion_matrix_note": None
        if effective.get("plots")
        else "plots=false，框架未计算混淆矩阵",
    }
    write_json(output / "evaluation.json", report)
    with (output / "results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["epoch", *metrics])
        writer.writeheader()
        writer.writerow({"epoch": 1, **metrics})
    resolution = {
        "contract_id": CONTRACT_ID,
        "framework_version": FRAMEWORK_VERSION,
        "submitted": config.get("submitted_parameters", parameters),
        "normalized": arguments,
        "effective": effective,
        "warnings": [],
    }
    write_json(WORKSPACE / "parameter-resolution.json", resolution)
    write_json(output / "parameter-resolution.json", resolution)
    write_json(
        WORKSPACE / "progress.json", {"phase": "epoch_end", "epoch": 1, "epochs": 1}
    )


def main() -> int:
    try:
        config = json.loads((WORKSPACE / "config.json").read_text(encoding="utf-8"))
        if config.get("operation") == "export":
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "platform_export", Path(__file__).with_name("export_model.py")
            )
            exporter = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(exporter)
            exporter.run_export(
                config, workspace=WORKSPACE, weights=WEIGHTS, input_weight=INPUT_WEIGHT,
                data=prepare_data(Path(config["dataset"])),
            )
        elif config.get("operation", "train") == "val":
            evaluate(config)
        else:
            train(config)
    except BaseException as exc:
        traceback.print_exc()
        write_json(
            WORKSPACE / "status.json",
            {
                "state": "failed",
                "error": friendly_error(exc),
                "finished_at": time.time(),
            },
        )
        return 1
    write_json(
        WORKSPACE / "status.json", {"state": "completed", "finished_at": time.time()}
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
