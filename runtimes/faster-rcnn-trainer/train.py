"""Offline Faster R-CNN trainer, same architecture and pixel contract as inference."""

import csv
import hashlib
import json
import os
import random
import signal
import time
import traceback
from pathlib import Path

import numpy as np
import torch
import yaml
from cv_algorithm_sdk.faster_rcnn import ARCHITECTURE, build_model, model_config
from cv_algorithm_sdk.training_contract import (
    FASTER_CONTRACT,
    FASTER_PROTOCOL,
    normalize_for,
)
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision.models.detection import FasterRCNN_ResNet50_FPN_Weights
from torchvision.ops import batched_nms, box_iou
from torchvision.transforms.functional import pil_to_tensor

ROOT = Path(os.environ.get("CV_TRAINING_WORKSPACE", "/workspace"))
OUTPUT = ROOT / "train"
STOP = False


def stopping(*_):
    global STOP
    STOP = True


def digest(path):
    with Path(path).open("rb") as reader:
        return hashlib.file_digest(reader, "sha256").hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    temporary.replace(path)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    torch.save(data, temporary)
    temporary.replace(path)


class DetectionDataset(Dataset):
    def __init__(self, root, split, names, flip=0):
        self.root, self.names, self.flip = Path(root), names, flip
        self.images = sorted(
            p for p in (self.root / "images" / split).rglob("*") if p.is_file()
        )
        if not self.images:
            raise ValueError(f"数据集没有 {split} 图片")

    def __len__(self):
        return len(self.images)

    def __getitem__(self, index):
        path = self.images[index]
        image = Image.open(path).convert("RGB")
        w, h = image.size
        label_path = (
            self.root
            / "labels"
            / path.relative_to(self.root / "images").with_suffix(".txt")
        )
        boxes, labels = [], []
        for line in label_path.read_text().splitlines() if label_path.is_file() else []:
            if not line.strip():
                continue
            category, x, y, bw, bh = map(float, line.split())
            if not category.is_integer() or not 0 <= category < len(self.names):
                raise ValueError("标注类别超出数据集类别范围")
            box = [
                max(0, (x - bw / 2) * w),
                max(0, (y - bh / 2) * h),
                min(w, (x + bw / 2) * w),
                min(h, (y + bh / 2) * h),
            ]
            if box[2] <= box[0] or box[3] <= box[1]:
                raise ValueError("存在无效检测框")
            boxes.append(box)
            labels.append(
                int(category) + 1
            )  # 0 is background, never a dataset category.
        boxes = torch.tensor(boxes, dtype=torch.float32).reshape(-1, 4)
        tensor = pil_to_tensor(image).float().div(255)
        if self.flip and random.random() < self.flip:
            tensor = tensor.flip(-1)
            boxes[:, [0, 2]] = w - boxes[:, [2, 0]]
        return tensor, {
            "boxes": boxes,
            "labels": torch.tensor(labels, dtype=torch.int64),
            "image_id": torch.tensor(index + 1),
            "iscrowd": torch.zeros(len(boxes), dtype=torch.int64),
            "area": (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1]),
        }


def collate(batch):
    return tuple(zip(*batch))


def load_initial(model, path, names, operation, source, builtin=False):
    state = torch.load(path, map_location="cpu", weights_only=True)
    if not builtin:
        if (
            not isinstance(state, dict)
            or state.get("architecture") != ARCHITECTURE
            or "class_names" not in state
        ):
            raise ValueError(
                "自定义 Faster R-CNN 权重须包含 architecture、class_names 和 state_dict；请使用平台发布或下载的权重"
            )
        source_names = state["class_names"]
        if (
            operation == "val" or (source and source.get("resume"))
        ) and source_names != names:
            raise ValueError("验证或续训要求权重类别名称和顺序与数据集完全一致")
        raw = state["state_dict"]
    else:
        raw = state
        source_names = list(FasterRCNN_ResNet50_FPN_Weights.COCO_V1.meta["categories"])[
            1:
        ]
    target = model.state_dict()
    # Rebuild the predictor by class name, including a single background row.
    for suffix in [
        "cls_score.weight",
        "cls_score.bias",
        "bbox_pred.weight",
        "bbox_pred.bias",
    ]:
        key = "roi_heads.box_predictor." + suffix
        mapped = target[key].clone()
        stride = 4 if suffix.startswith("bbox_pred") else 1
        for dest, name in enumerate(["__background__"] + names):
            old = (
                0
                if dest == 0
                else source_names.index(name) + 1
                if name in source_names
                else None
            )
            if old is not None:
                mapped[dest * stride : (dest + 1) * stride] = raw[key][
                    old * stride : (old + 1) * stride
                ]
        raw[key] = mapped
    model.load_state_dict(raw, strict=True)
    return state


def evaluate(model, dataset, device, parameters, names, weight=None):
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval

    model.eval()
    annotations, images, predictions = [], [], []
    tp = np.zeros(len(names), dtype=int)
    fp = tp.copy()
    fn = tp.copy()
    confusion = np.zeros((len(names) + 1, len(names) + 1), dtype=int)
    annotation_id = 0
    inference_seconds = 0
    loader = DataLoader(
        dataset,
        batch_size=parameters["batch"],
        num_workers=parameters["workers"],
        collate_fn=collate,
    )
    for inputs, targets in loader:
        if STOP:
            raise InterruptedError("用户停止任务")
        if device.type == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            results = model([x.to(device) for x in inputs])
        if device.type == "cuda":
            torch.cuda.synchronize()
        inference_seconds += time.perf_counter() - started
        for image, target, result in zip(inputs, targets, results, strict=True):
            result = {k: v.cpu() for k, v in result.items()}
            keep = batched_nms(
                result["boxes"], result["scores"], result["labels"], parameters["iou"]
            )
            keep = keep[result["scores"][keep] >= parameters["conf"]][
                : parameters["max_det"]
            ]
            result = {k: v[keep] for k, v in result.items()}
            image_id = int(target["image_id"])
            h, w = image.shape[-2:]
            images.append({"id": image_id, "height": h, "width": w})
            for box, label, area in zip(
                target["boxes"], target["labels"], target["area"], strict=True
            ):
                annotation_id += 1
                x1, y1, x2, y2 = box.tolist()
                annotations.append(
                    {
                        "id": annotation_id,
                        "image_id": image_id,
                        "category_id": int(label),
                        "bbox": [x1, y1, x2 - x1, y2 - y1],
                        "area": float(area),
                        "iscrowd": 0,
                    }
                )
            for box, label, score in zip(
                result["boxes"], result["labels"], result["scores"], strict=True
            ):
                x1, y1, x2, y2 = box.tolist()
                predictions.append(
                    {
                        "image_id": image_id,
                        "category_id": int(label),
                        "bbox": [x1, y1, x2 - x1, y2 - y1],
                        "score": float(score),
                    }
                )
            # P/R and confusion use a documented confidence 0.25 and IoU 0.5.
            used = set()
            matrix_used = set()
            for j in torch.where(result["scores"] >= 0.25)[0].tolist():
                label = int(result["labels"][j]) - 1
                overlaps = box_iou(result["boxes"][j : j + 1], target["boxes"])[0]
                candidates = [
                    i
                    for i in range(len(overlaps))
                    if i not in used
                    and overlaps[i] >= 0.5
                    and target["labels"][i] == label + 1
                ]
                if candidates:
                    best = max(candidates, key=lambda i: float(overlaps[i]))
                    used.add(best)
                    tp[label] += 1
                else:
                    fp[label] += 1
                candidates = [
                    i
                    for i in range(len(overlaps))
                    if i not in matrix_used and overlaps[i] >= 0.5
                ]
                if candidates:
                    best = max(candidates, key=lambda i: float(overlaps[i]))
                    matrix_used.add(best)
                    confusion[label, int(target["labels"][best]) - 1] += 1
                else:
                    confusion[label, -1] += 1
            for i, label in enumerate(target["labels"].tolist()):
                if i not in used:
                    fn[label - 1] += 1
                if i not in matrix_used:
                    confusion[-1, label - 1] += 1
    coco = COCO()
    coco.dataset = {
        "info": {},
        "images": images,
        "annotations": annotations,
        "categories": [{"id": i + 1, "name": name} for i, name in enumerate(names)],
    }
    coco.createIndex()
    if predictions:
        detected = coco.loadRes(predictions)
    else:
        detected = COCO()
        detected.dataset = {**coco.dataset, "annotations": []}
        detected.createIndex()
    evaluator = COCOeval(coco, detected, "bbox")
    evaluator.params.imgIds = [im["id"] for im in images]
    evaluator.evaluate()
    evaluator.accumulate()
    evaluator.summarize()
    precision = evaluator.eval["precision"]

    def mean_valid(values):
        valid = values[values >= 0]
        return float(valid.mean()) if valid.size else None

    p = float(tp.sum() / max(1, (tp + fp).sum()))
    r = float(tp.sum() / max(1, (tp + fn).sum()))
    metrics = {
        "metrics/precision(B)": p,
        "metrics/recall(B)": r,
        "metrics/mAP50(B)": mean_valid(precision[0, :, :, 0, -1]),
        "metrics/mAP50-95(B)": mean_valid(precision[:, :, :, 0, -1]),
    }
    per_class = []
    for i, name in enumerate(names):
        if tp[i] + fn[i] == 0:
            continue
        cp = float(tp[i] / max(1, tp[i] + fp[i]))
        cr = float(tp[i] / max(1, tp[i] + fn[i]))
        per_class.append(
            {
                "class_id": i,
                "name": name,
                "instances": int(tp[i] + fn[i]),
                "images": len(
                    {a["image_id"] for a in annotations if a["category_id"] == i + 1}
                ),
                "precision": cp,
                "recall": cr,
                "f1": 2 * cp * cr / (cp + cr) if cp + cr else 0.0,
                "map50": mean_valid(precision[0, :, i, 0, -1]),
                "map50_95": mean_valid(precision[:, :, i, 0, -1]),
            }
        )
    return {
        "framework_version": "torchvision 0.25.0",
        "split": parameters.get("split", "val"),
        "checkpoint": "best",
        "metrics": metrics,
        "per_class": per_class,
        "speed": {"inference": inference_seconds * 1000 / len(images)},
        "confusion_matrix": confusion.tolist(),
        "confusion_matrix_note": "行=预测、列=真实，最后一行/列为背景；P/R 与混淆矩阵：置信度≥0.25、IoU≥0.5；COCO AP：IoU 0.50:0.95，maxDets=100。",
        "weight_sha256": digest(weight) if weight else None,
    }


def plots(history, evaluation, names):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    for key in [
        "train/box_loss",
        "train/cls_loss",
        "train/rpn_box_loss",
        "train/objectness_loss",
    ]:
        axes[0].plot(
            [r["epoch"] for r in history], [r.get(key, 0) for r in history], label=key
        )
    for key in ["metrics/mAP50(B)", "metrics/mAP50-95(B)"]:
        axes[1].plot(
            [r["epoch"] for r in history], [r.get(key, 0) for r in history], label=key
        )
    for ax in axes:
        ax.legend(fontsize=7)
        ax.set_xlabel("epoch")
        ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(OUTPUT / "results.png", dpi=150)
    plt.close(fig)
    matrix = np.array(evaluation["confusion_matrix"])
    # Hide classes absent from both axes for readable COCO-sized plots.
    present = np.array(
        sorted(set(np.where((matrix.sum(0) + matrix.sum(1)) > 0)[0]) | {len(names)})
    )
    present = present[:60]
    fig, ax = plt.subplots(figsize=(9, 8))
    ax.imshow(matrix[np.ix_(present, present)], cmap="Blues")
    labels = names + ["background"]
    ax.set_xticks(range(len(present)), [labels[i] for i in present], rotation=90)
    ax.set_yticks(range(len(present)), [labels[i] for i in present])
    ax.set_xlabel("True")
    ax.set_ylabel("Predicted")
    fig.tight_layout()
    fig.savefig(OUTPUT / "confusion_matrix.png", dpi=150)
    plt.close(fig)


def run(config):
    import torchvision

    if not torch.__version__.startswith(
        "2.10.0"
    ) or not torchvision.__version__.startswith("0.25.0"):
        raise ValueError("Faster R-CNN 运行环境要求 torch 2.10.0 / torchvision 0.25.0")
    if config.get("parameter_contract_id") != FASTER_CONTRACT:
        raise ValueError("训练参数契约不匹配")
    operation = config.get("operation", "train")
    params = normalize_for(ARCHITECTURE, operation, config["submitted_parameters"])
    device = torch.device("cpu" if config["device"] == "cpu" else "cuda:0")
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError("所选 GPU 不可用")
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    seed = params.get("seed", 0)
    torch.manual_seed(seed)
    random.seed(seed)
    data = yaml.safe_load((Path(config["dataset"]) / "data.yaml").read_text())
    names = data["names"]
    names = [names[k] for k in sorted(names)] if isinstance(names, dict) else names
    inference_config = model_config(names, params["imgsz"], params["max_size"])
    model = build_model(inference_config["model"])
    source = config.get("training_input")
    weight = Path("/input/model.pt") if source else Path("/opt/weights") / ARCHITECTURE
    state = {}
    if source or params.get("pretrained", True) or operation == "val":
        if source and digest(weight) != source["sha256"]:
            raise ValueError("输入权重哈希不一致")
        state = load_initial(
            model, weight, names, operation, source, builtin=not source
        )
    model.to(device)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write(OUTPUT / "model-config.json", inference_config)
    resolution = {
        "contract_id": FASTER_CONTRACT,
        "framework_version": "torchvision 0.25.0",
        "submitted": config["submitted_parameters"],
        "normalized": params,
        "effective": {
            **params,
            "device": str(device),
            "num_classes": len(names) + 1,
            "architecture": ARCHITECTURE,
        },
        "warnings": [
            "P/R 为固定置信度 0.25、IoU 0.5；与 YOLO 默认 P/R 工作点可能不同。"
        ],
        "initial_weights": {"file": str(weight), "sha256": digest(weight)}
        if weight.is_file() and (source or params.get("pretrained", True))
        else None,
    }
    write(ROOT / "parameter-resolution.json", resolution)
    write(OUTPUT / "parameter-resolution.json", resolution)
    validation = DetectionDataset(config["dataset"], params.get("split", "val"), names)
    history = []
    best = -1.0
    start_epoch = 0
    if operation == "train":
        layers = ["layer4", "layer3", "layer2", "layer1", "conv1"][
            : params["trainable_backbone_layers"]
        ]
        for name, p in model.backbone.body.named_parameters():
            p.requires_grad_(any(name.startswith(prefix) for prefix in layers))
        trainable = [p for p in model.parameters() if p.requires_grad]
        optimizer = (
            torch.optim.SGD(
                trainable,
                lr=params["lr0"],
                momentum=params["momentum"],
                weight_decay=params["weight_decay"],
            )
            if params["optimizer"] == "SGD"
            else torch.optim.AdamW(
                trainable, lr=params["lr0"], weight_decay=params["weight_decay"]
            )
        )
        scheduler = torch.optim.lr_scheduler.StepLR(
            optimizer, params["step_size"], params["gamma"]
        )
        if source and source.get("resume"):
            if (
                state.get("protocol") != FASTER_PROTOCOL
                or state.get("dataset_sha256") != config["dataset_sha256"]
            ):
                raise ValueError("恢复协议或数据哈希不匹配")
            optimizer.load_state_dict(state["optimizer"])
            scheduler.load_state_dict(state["scheduler"])
            torch.set_rng_state(state["torch_rng"])
            random.setstate(state["random_rng"])
            if device.type == "cuda" and state.get("cuda_rng") is not None:
                torch.cuda.set_rng_state_all(state["cuda_rng"])
            start_epoch = state["epoch"]
            history = state["history"]
            best = state["best_fitness"]
            if Path("/input/best.pt").is_file():
                import shutil

                if digest("/input/best.pt") != source.get("best_sha256"):
                    raise ValueError("最佳权重哈希不一致")
                (OUTPUT / "weights").mkdir(exist_ok=True)
                shutil.copyfile("/input/best.pt", OUTPUT / "weights/best.pt")
        training = DetectionDataset(config["dataset"], "train", names, params["fliplr"])
        for epoch in range(start_epoch, params["epochs"]):
            generator = torch.Generator().manual_seed(seed + epoch)
            loader = DataLoader(
                training,
                batch_size=params["batch"],
                shuffle=True,
                num_workers=params["workers"],
                collate_fn=collate,
                generator=generator,
            )
            model.train()
            # Frozen backbone blocks must not change BatchNorm statistics.
            for name, module in model.backbone.body.named_modules():
                if isinstance(module, torch.nn.BatchNorm2d) and not any(
                    p.requires_grad for p in module.parameters()
                ):
                    module.eval()
            losses = {}
            started = time.time()
            for batch, (inputs, targets) in enumerate(loader):
                if STOP:
                    raise InterruptedError("用户停止任务")
                loss_dict = model(
                    [x.to(device) for x in inputs],
                    [{k: v.to(device) for k, v in t.items()} for t in targets],
                )
                loss = sum(loss_dict.values())
                if not torch.isfinite(loss):
                    raise ValueError("损失非有限值，请检查数据和学习率")
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                for key, value in loss_dict.items():
                    losses[key] = losses.get(key, 0) + float(value.detach())
                write(
                    ROOT / "progress.json",
                    {
                        "phase": "batch_end",
                        "epoch": epoch,
                        "epochs": params["epochs"],
                        "batch": batch + 1,
                        "batches": len(loader),
                        "elapsed_seconds": time.time() - started,
                    },
                )
            scheduler.step()
            evaluation = evaluate(model, validation, device, params, names)
            row = {
                "epoch": epoch + 1,
                **evaluation["metrics"],
                **{
                    new: losses[old] / len(loader)
                    for old, new in [
                        ("loss_box_reg", "train/box_loss"),
                        ("loss_classifier", "train/cls_loss"),
                        ("loss_rpn_box_reg", "train/rpn_box_loss"),
                        ("loss_objectness", "train/objectness_loss"),
                    ]
                },
            }
            history.append(row)
            checkpoint = {
                "architecture": ARCHITECTURE,
                "state_dict": model.state_dict(),
                "class_names": names,
                "model_config": inference_config,
                "epoch": epoch + 1,
                "parameters": params,
            }
            save(OUTPUT / "weights/last.pt", checkpoint)
            score = row["metrics/mAP50-95(B)"]
            if score is None:
                raise ValueError("验证集没有可评估目标")
            if score >= best:
                best = score
                save(OUTPUT / "weights/best.pt", checkpoint)
            resume = {
                **checkpoint,
                "protocol": FASTER_PROTOCOL,
                "dataset_sha256": config["dataset_sha256"],
                "optimizer": optimizer.state_dict(),
                "scheduler": scheduler.state_dict(),
                "torch_rng": torch.get_rng_state(),
                "random_rng": random.getstate(),
                "cuda_rng": torch.cuda.get_rng_state_all()
                if device.type == "cuda"
                else None,
                "history": history,
                "best_fitness": best,
            }
            save(OUTPUT / "weights/resume.pt", resume)
            write(
                OUTPUT / "checkpoint.json",
                {
                    "file": "weights/resume.pt",
                    "sha256": digest(OUTPUT / "weights/resume.pt"),
                    "protocol": FASTER_PROTOCOL,
                    "dataset_sha256": config["dataset_sha256"],
                    "epoch": epoch + 1,
                    "epochs": params["epochs"],
                    "optimizer": params["optimizer"],
                    "lr0": params["lr0"],
                    "resumable": epoch + 1 < params["epochs"],
                    "reason": None
                    if epoch + 1 < params["epochs"]
                    else "已完成全部训练轮次，请使用权重微调",
                },
            )
            with (OUTPUT / "results.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(row))
                writer.writeheader()
                writer.writerows(history)
            write(
                ROOT / "progress.json",
                {"phase": "epoch_end", "epoch": epoch + 1, "epochs": params["epochs"]},
            )
            print(
                f"Epoch {epoch + 1}/{params['epochs']}: mAP50-95={score:.5f}",
                flush=True,
            )
        load_initial(model, OUTPUT / "weights/best.pt", names, "val", None)
        weight = OUTPUT / "weights/best.pt"
    evaluation = evaluate(model, validation, device, params, names, weight)
    evaluation["dataset_sha256"] = config["dataset_sha256"]
    write(OUTPUT / "evaluation.json", evaluation)
    if params["plots"]:
        plots(history, evaluation, names)
    write(
        ROOT / "progress.json",
        {
            "phase": "epoch_end",
            "epoch": params.get("epochs", 1),
            "epochs": params.get("epochs", 1),
        },
    )
    write(ROOT / "status.json", {"state": "completed"})


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    try:
        run(json.loads((ROOT / "config.json").read_text()))
    except InterruptedError:
        write(ROOT / "status.json", {"state": "stopped"})
    except Exception as exc:  # noqa: BLE001 - persist terminal errors for the manager
        traceback.print_exc()
        write(ROOT / "status.json", {"state": "failed", "error": str(exc)[:2000]})
        raise SystemExit(1)
