"""Algorithm-specific training contracts; torch is only imported by runtimes."""

import hashlib
import json

from jsonschema import Draft202012Validator

from . import runtime_contract as yolo
from .training_sources import TRAINING_PROTOCOL

FASTER_MODEL = "fasterrcnn_resnet50_fpn.pt"
FASTER_CONTRACT = "torchvision:0.25.0:fasterrcnn-resnet50-fpn:v1"
FASTER_PROTOCOL = "torchvision:0.25.0:fasterrcnn-sources-v1"
FASTER_RESUME_OVERRIDES = frozenset({"batch", "workers", "device", "plots"})


def is_faster(model):
    return model == FASTER_MODEL


def protocol_for(model):
    return FASTER_PROTOCOL if is_faster(model) else TRAINING_PROTOCOL


def contract_for(model, mode="train"):
    if not is_faster(model):
        return yolo.get_contract(mode)
    if mode not in {"train", "val"}:
        raise ValueError("Faster R-CNN 当前支持训练和独立验证，不支持此操作")
    specs = {
        "batch": (2, "批量大小", {"type": "integer", "minimum": 1, "maximum": 64}),
        "imgsz": (600, "短边尺寸", {"type": "integer", "minimum": 64, "maximum": 2048}),
        "max_size": (
            1000,
            "长边上限",
            {"type": "integer", "minimum": 64, "maximum": 4096},
        ),
        "workers": (
            2,
            "数据加载进程",
            {"type": "integer", "minimum": 0, "maximum": 32},
        ),
        "device": ("auto", "执行设备", {"enum": ["auto", "cpu"]}),
        "conf": (
            0.001,
            "验证最低置信度",
            {"type": "number", "minimum": 0, "maximum": 1},
        ),
        "iou": (0.5, "分类别 NMS 阈值", {"type": "number", "minimum": 0, "maximum": 1}),
        "max_det": (
            100,
            "每图最多检测数",
            {"type": "integer", "minimum": 1, "maximum": 300},
        ),
        "plots": (True, "保存结果图表", {"type": "boolean"}),
    }
    if mode == "train":
        specs = {
            "epochs": (
                20,
                "训练轮数",
                {"type": "integer", "minimum": 1, "maximum": 10000},
            ),
            "lr0": (
                0.005,
                "初始学习率",
                {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
            ),
            "optimizer": ("SGD", "优化器", {"enum": ["SGD", "AdamW"]}),
            "momentum": (
                0.9,
                "SGD 动量",
                {"type": "number", "minimum": 0, "exclusiveMaximum": 1},
            ),
            "weight_decay": (
                0.0005,
                "权重衰减",
                {"type": "number", "minimum": 0, "maximum": 1},
            ),
            "step_size": (
                8,
                "学习率衰减间隔",
                {"type": "integer", "minimum": 1, "maximum": 10000},
            ),
            "gamma": (
                0.1,
                "学习率衰减倍率",
                {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
            ),
            "trainable_backbone_layers": (
                3,
                "可训练骨干层数",
                {"type": "integer", "minimum": 0, "maximum": 5},
            ),
            "fliplr": (
                0.5,
                "水平翻转概率",
                {"type": "number", "minimum": 0, "maximum": 1},
            ),
            "seed": (
                0,
                "随机种子",
                {"type": "integer", "minimum": 0, "maximum": 2147483647},
            ),
            "pretrained": (True, "加载预训练权重", {"type": "boolean"}),
            **specs,
        }
    else:
        specs["split"] = ("val", "验证划分", {"enum": ["train", "val", "test"]})
    entries = {
        k: {
            "type": "json",
            "default": default,
            "json_schema": schema,
            "title": title,
            "description": title,
            "group": "训练参数" if mode == "train" else "验证参数",
            "common": k in {"epochs", "batch", "imgsz", "max_size", "lr0", "split"},
            "status": "configurable",
            "reason": None,
        }
        for k, (default, title, schema) in specs.items()
    }
    entries["device"]["description"] = (
        "auto 使用所选 GPU；cpu 显式使用 CPU，适合小数据验证，训练速度较慢。"
    )
    entries["imgsz"]["description"] = (
        "保持宽高比缩放短边，同时受长边上限约束；训练发布时同步给推理。"
    )
    body = {
        "id": FASTER_CONTRACT,
        "framework": "torchvision",
        "framework_version": "0.25.0",
        "mode": mode,
        "task": "detect",
        "parameters": entries,
        "default_key_count": len(entries),
    }
    body["sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()
    ).hexdigest()
    return body


def normalize_for(model, mode, parameters):
    if not is_faster(model):
        return yolo.normalize_parameters(mode, parameters)
    contract = contract_for(model, mode)
    entries = contract["parameters"]
    unknown = set(parameters) - entries.keys()
    if unknown:
        raise ValueError(f"Faster R-CNN 不支持参数：{', '.join(sorted(unknown))}")
    result = {k: v["default"] for k, v in entries.items()} | parameters
    for key, value in result.items():
        errors = list(
            Draft202012Validator(entries[key]["json_schema"]).iter_errors(value)
        )
        if errors:
            raise ValueError(f"{key}: {errors[0].message}")
    if mode == "train" and not result["pretrained"]:
        if "trainable_backbone_layers" not in parameters:
            result["trainable_backbone_layers"] = 5
        elif result["trainable_backbone_layers"] != 5:
            raise ValueError("从头训练须训练全部 5 个骨干层")
    if result["max_size"] < result["imgsz"]:
        raise ValueError("长边上限 max_size 不能小于短边尺寸 imgsz")
    return result
