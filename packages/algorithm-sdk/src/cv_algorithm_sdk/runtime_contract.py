"""Pinned Ultralytics contract; no torch/ultralytics import in the control plane.

The packaged catalog records all default.cfg keys and API-only arguments. Availability
is explicit: a catalog entry is not a promise that the current adapter can execute it.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
from typing import Any

from .parameters import canonical_value, json_value, validate_value

FRAMEWORK_VERSION = "8.4.183"
CONTRACT_ID = f"ultralytics:{FRAMEWORK_VERSION}:parameters-v1"
TRAINING_DEFAULTS = {
    "epochs": 100, "batch": 16, "imgsz": 640, "lr0": 0.01, "optimizer": "auto",
    "patience": 50, "workers": 4, "seed": 0, "cos_lr": False,
}
PREDICTION_ALIASES = {
    "confidence": "conf", "iou_threshold": "iou", "max_detections": "max_det",
    "image_size": "imgsz",
}
COMMON_TRAINING = tuple(TRAINING_DEFAULTS)
COMMON_PREDICTION = ("conf", "iou", "max_det", "imgsz", "classes", "agnostic_nms")
COMMON_VALIDATION = ("split", "batch", "imgsz", "conf", "iou", "classes", "plots", "workers")
COMMON_EXPORT = ("format", "imgsz", "batch", "dynamic", "simplify", "opset", "quantize")
EXPORT_DESCRIPTIONS = {
    "imgsz": "整数或 [高度, 宽度]，均为 32 的倍数；静态导出要求正方形，矩形请选择动态导出。",
    "batch": "固定整数 1～64；静态图的验证图片数须整除 batch，动态导出允许末尾不足一个 batch。",
    "quantize": "当前 ONNX 转换与验证仅支持 FP32；null、32、fp32 和 w32a32 等价。",
    "dynamic": "导出动态 batch 和空间尺寸；实际兼容形状由模型及目标运行时共同决定。",
}
MANAGED = {
    "task": "任务类型由算法适配器确定", "mode": "运行模式由任务入口确定",
    "model": "通过模型选择器配置", "data": "通过数据集选择器配置",
    "device": "通过服务器和 GPU 选择器配置", "source": "通过输入素材选择器配置",
    "project": "平台映射到本次任务输出目录", "name": "平台映射到本次任务名称/目录",
    "save_dir": "平台管理产物目录", "exist_ok": "每次任务使用独立目录",
    "cfg": "请导入专家配置内容，容器内配置文件路径由平台管理",
    "resume": "通过训练记录的断点续训入口选择完整 checkpoint；固定原数据和训练计划",
}
TRAINING_UNAVAILABLE = {
    "distill_model": "教师模型需要受管模型资源，将在模型选择阶段接入",
    "augmentations": "自定义增强实例需要插件适配；当前可配置内置增强参数",
    "quantize": "QAT 训练需要专用运行环境，将在量化阶段接入；训练精度使用 amp",
    "profile": "训练时跨格式 profiling 需要导出环境，将在导出阶段接入",
}
PREDICTION_UNAVAILABLE = {
    "batch": "当前接口每次处理一张图片，模型 batch 将在批量推理阶段接入",
    "stream": "当前接口返回单张图片结果，流式任务将在视频阶段接入",
    "vid_stride": "视频抽帧由媒体任务配置", "stream_buffer": "流缓冲需视频任务生命周期",
    "embed": "特征向量需要对应结果协议，将在特征任务阶段接入",
    "retina_masks": "当前 YOLO 适配器是目标检测，分割适配器接入后可用",
    "dnn": "此适配器使用 PyTorch 权重，OpenCV DNN 需要 ONNX 后端",
    "data": "类别名称来自模型元数据，无需传入数据集",
    "show": "服务端不创建桌面窗口，结果在工作台展示",
    "save": "图片与结果由平台任务存储管理", "save_frames": "视频任务尚未接入",
    "save_txt": "结果导出由平台任务管理", "save_conf": "结果导出由平台任务管理",
    "save_crop": "裁剪产物需要任务产物协议，将在推理流程阶段接入",
    "show_labels": "渲染选项由工作台结果查看器配置",
    "show_conf": "渲染选项由工作台结果查看器配置",
    "show_boxes": "渲染选项由工作台结果查看器配置",
    "line_width": "渲染选项由工作台结果查看器配置",
    "visualize": "特征可视化需要额外产物协议，将在解释性分析阶段接入",
    "verbose": "请求日志由平台管理",
}
TASK_FIELDS = {
    **dict.fromkeys(("overlap_mask", "mask_ratio", "retina_masks"), ("segment",)),
    **dict.fromkeys(("dropout", "auto_augment", "erasing"), ("classify",)),
    **dict.fromkeys(("pose", "kobj", "rle"), ("pose",)),
    "angle": ("obb",),
    **dict.fromkeys(("dlog", "dgrad", "dlam"), ("depth",)),
    **dict.fromkeys(("copy_paste", "copy_paste_mode"), ("segment", "semantic", "obb")),
}
TITLES = dict(zip(
    ["task", "mode", "model", "data", "epochs", "time", "patience", "batch", "imgsz", "save", "save_period", "cache", "device", "workers", "project", "name", "exist_ok", "pretrained", "cls_remap", "optimizer", "verbose", "seed", "deterministic", "single_cls", "rect", "cos_lr", "close_mosaic", "resume", "amp", "fraction", "profile", "freeze", "multi_scale", "compile", "channels_last", "overlap_mask", "mask_ratio", "dropout", "val", "split", "save_json", "conf", "iou", "max_det", "quantize", "dnn", "plots", "nms", "source", "vid_stride", "stream_buffer", "visualize", "augment", "agnostic_nms", "classes", "retina_masks", "embed", "show", "save_frames", "save_txt", "save_conf", "save_crop", "show_labels", "show_conf", "show_boxes", "line_width", "format", "optimize", "dynamic", "simplify", "opset", "workspace", "lr0", "lrf", "momentum", "weight_decay", "warmup_epochs", "warmup_momentum", "warmup_bias_lr", "distill_model", "dis", "box", "cls", "cls_pw", "dfl", "pose", "kobj", "rle", "angle", "dlog", "dgrad", "dlam", "nbs", "hsv_h", "hsv_s", "hsv_v", "degrees", "translate", "scale", "shear", "perspective", "flipud", "fliplr", "bgr", "mosaic", "mixup", "cutmix", "copy_paste", "copy_paste_mode", "auto_augment", "erasing", "cfg", "tracker", "stream", "save_dir", "augmentations", "eps", "persist", "iterations", "use_ray", "space"],
    ["任务", "模式", "模型", "数据集", "训练轮数", "训练时限", "提前停止", "批大小", "输入尺寸", "保存结果", "定期保存", "图片缓存", "计算设备", "读图进程", "输出项目", "运行名称", "允许覆盖", "预训练", "类别重映射", "优化器", "详细日志", "随机种子", "确定性计算", "单类别训练", "矩形输入", "余弦学习率", "关闭拼接轮数", "断点续训", "混合精度", "数据子集", "训练性能分析", "冻结层", "多尺度范围", "图编译", "内存布局", "重叠掩码", "掩码下采样", "随机失活", "训练验证", "验证划分", "保存JSON", "置信度阈值", "NMS交并比", "最大目标数", "量化精度", "OpenCV后端", "生成图表", "NMS模式", "输入来源", "视频步长", "流缓冲", "特征可视化", "测试时增强", "类别无关NMS", "类别筛选", "高精度掩码", "特征层", "显示窗口", "保存帧", "保存文本", "保存置信度", "保存裁剪", "显示类别", "显示分数", "显示框", "线宽", "导出格式", "编译优化", "动态尺寸", "图简化", "算子集", "工作空间", "初始学习率", "最终学习率比例", "动量", "权重衰减", "预热轮数", "预热动量", "预热偏置学习率", "教师模型", "蒸馏损失", "框损失", "分类损失", "类别权重幂", "位置分布损失", "姿态损失", "关键点置信度损失", "关键点回归损失", "旋转角损失", "深度对数损失", "深度梯度损失", "深度方差权重", "标称批大小", "色调增强", "饱和度增强", "明度增强", "旋转范围", "平移范围", "缩放范围", "错切范围", "透视变换", "垂直翻转", "水平翻转", "通道交换", "拼接增强", "混合增强", "裁块增强", "复制粘贴增强", "复制粘贴模式", "自动增强策略", "随机擦除", "配置文件", "跟踪器", "流式输出", "保存目录", "自定义增强", "基准微量修正", "跟踪状态保持", "调参轮数", "Ray调参", "搜索空间"], strict=True,
))
DESCRIPTIONS = {
    "batch": "正整数为批大小；-1 自动选择；0~1 之间的小数表示显存占比。多卡必须使用正整数。",
    "lr0": "初始学习率。optimizer=auto 时由框架自动决定，不会使用手填值。",
    "time": "最长训练时长，单位小时；设置后框架按时限调整总轮数。null 表示不限时。",
    "patience": "连续多少轮没有提升后停止；0 表示关闭提前停止。",
    "freeze": "冻结前 N 层，或提供层编号/模块名称列表；null 表示不冻结。",
    "classes": "类别编号数组，如 [0, 2]；null 表示全部类别。",
    "fraction": "支持数据比例、图片数量或 [train, val, test] 设置；test 条目允许 0。",
    "amp": "true/fp16 启用 FP16 自动混合精度；bf16 使用 BF16；false/fp32 使用 FP32。",
    "cache": "false 不缓存；true/ram 缓存在内存；disk 缓存到本次训练的可写数据副本。",
    "optimizer": "自动选择或显式指定优化器；自动模式会调整实际学习率。",
    "quantize": "统一精度选项；推理支持 FP16/FP32，INT8 需要已量化产物，不能临时转换 .pt。",
    "nms": "null 使用默认检测头；true 使用常规 NMS；false 在模型支持时使用无 NMS 头。",
    "scale": "可用单个缩放范围，或 [最小值, 最大值]；显式范围必须从小到大。",
    "save": "控制常规 checkpoint 保存；关闭后框架仍可能在最后一轮保存，中途停止可能没有权重。",
    "val": "控制逐轮验证；关闭后框架仍可能执行最终验证或提前停止检查。",
    "dis": "蒸馏损失权重；需要教师模型，当前入口尚未接入教师模型选择。",
    "compile": "false 禁用；true 使用默认编译；也可指定受支持的 torch.compile 模式。",
    "imgsz": "训练使用单个尺寸；预测可使用整数或 [高度, 宽度]，尺寸为 32 的倍数。",
}


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    return json.loads(files(__package__).joinpath("ultralytics-8.4.183.json").read_text("utf-8"))


def _schema(name: str, default: Any) -> dict[str, Any]:
    positive_int = {"type": "integer", "minimum": 1}
    fraction = {"anyOf": [{"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                           {"type": "integer", "minimum": 2}]}
    if name == "batch":
        return {"anyOf": [positive_int, {"const": -1},
                           {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}]}
    if name == "fraction":
        return {"anyOf": [fraction, {"type": "array", "minItems": 2, "maxItems": 3,
            "prefixItems": [fraction, fraction, {"anyOf": [fraction, {"const": 0}]}],
            "items": False}]}
    if name in {"classes", "embed"}:
        return {"anyOf": [{"type": "null"}, {"type": "array", "items": {
            "type": "integer", "minimum": 0}, "minItems": 1, "uniqueItems": True}]}
    if name == "freeze":
        return {"anyOf": [{"type": "null"}, {"type": "integer", "minimum": 0},
            {"type": "array", "items": {"anyOf": [{"type": "integer", "minimum": 0},
                {"type": "string", "minLength": 1}]}, "minItems": 1}]}
    if name == "imgsz":
        size = {"type": "integer", "minimum": 32, "multipleOf": 32}
        return {"anyOf": [size, {"type": "array", "items": size, "minItems": 2,
                                "maxItems": 2}]}
    if name == "amp":
        return {"enum": [True, False, "fp16", "bf16", "fp32"]}
    if name == "cache":
        return {"enum": [False, True, "ram", "disk"]}
    if name == "compile":
        return {"enum": [False, True, "default", "reduce-overhead", "max-autotune",
                         "max-autotune-no-cudagraphs"]}
    if name == "quantize":
        return {"enum": [None, 8, 16, 32, "int8", "fp16", "fp32", "w8a8", "w16a16",
                         "w32a32", "w8a16", "w8a32"]}
    if name == "optimizer":
        return {"type": "string", "enum": ["auto", "SGD", "MuSGD", "Adam", "Adamax",
                                            "AdamW", "NAdam", "RAdam", "RMSProp"]}
    if name == "scale":
        return {"anyOf": [{"type": "number", "minimum": 0}, {"type": "array",
            "items": {"type": "number", "exclusiveMinimum": 0}, "minItems": 2, "maxItems": 2}]}
    if name in {"nms", "channels_last"}:
        return {"type": ["boolean", "null"]}
    if name in {"time", "workspace"}:
        return {"type": ["number", "null"], "exclusiveMinimum": 0}
    if name in {"opset", "line_width"}:
        return {"type": ["integer", "null"], "minimum": 1}
    if name == "pretrained":
        return {"anyOf": [{"type": "boolean"}, {"type": "string", "minLength": 1}]}
    if name in {"augmentations", "space"}:
        return {"type": ["array" if name == "augmentations" else "object", "null"]}
    if name == "device":
        return {"anyOf": [{"type": ["integer", "string", "null"]},
                           {"type": "array", "items": {"type": ["integer", "string"]}}]}
    if type(default) is bool:
        return {"type": "boolean"}
    if type(default) is int:
        minimum = -1 if name == "save_period" else 0 if name in {
            "workers", "seed", "patience", "close_mosaic"} else 1
        return {"type": "integer", "minimum": minimum}
    if type(default) is float:
        schema = {"type": "number", "minimum": 0}
        if name in {"lr0", "lrf", "momentum", "weight_decay", "warmup_momentum",
                    "warmup_bias_lr", "cls_pw", "dropout", "hsv_h", "hsv_s", "hsv_v",
                    "translate", "perspective", "flipud", "fliplr", "bgr", "mosaic",
                    "mixup", "cutmix", "copy_paste", "erasing", "conf", "iou", "multi_scale", "dlam"}:
            schema["maximum"] = 1
        return schema
    if name == "conf":
        return {"type": ["number", "null"], "minimum": 0, "maximum": 1}
    if default is None:
        return {"type": ["string", "null"]}
    schema = {"type": "string"}
    enums = {"split": ["train", "val", "test"], "copy_paste_mode": ["flip", "mixup"],
             "auto_augment": ["randaugment", "autoaugment", "augmix"]}
    if name in enums:
        schema["enum"] = enums[name]
    return schema


def _group(name: str) -> str:
    if name in COMMON_TRAINING:
        return "常用参数"
    if name in {"lrf", "momentum", "weight_decay", "warmup_epochs", "warmup_momentum",
                "warmup_bias_lr", "nbs"}:
        return "优化与调度"
    if name in {"hsv_h", "hsv_s", "hsv_v", "degrees", "translate", "scale", "shear",
                "perspective", "flipud", "fliplr", "bgr", "mosaic", "mixup", "cutmix",
                "copy_paste", "copy_paste_mode", "auto_augment", "erasing", "augmentations",
                "close_mosaic"}:
        return "数据增强"
    if name in TASK_FIELDS or name in {"box", "cls", "cls_pw", "dfl", "dis", "distill_model"}:
        return "损失与任务"
    if name.startswith("save") or name in {"plots", "val", "verbose"}:
        return "验证与输出"
    if name in {"format", "dynamic", "opset", "workspace", "simplify", "optimize"}:
        return "导出配置"
    return "运行与精度"


def get_contract(mode: str = "train", task: str = "detect") -> dict[str, Any]:
    if mode not in {"train", "val", "predict", "export", "track", "benchmark", "tune"}:
        raise ValueError(f"unsupported mode: {mode}")
    entries = {}
    for name, original in _catalog()["parameters"].items():
        modes = set(original["modes"])
        if "val" in modes:
            modes.add("train")  # train's inline validator accepts validation overrides
        if mode == "track" and "predict" in modes:
            modes.add("track")
        if mode not in modes:
            continue
        default = deepcopy(original["default"])
        if mode == "train" and name in TRAINING_DEFAULTS:
            default = TRAINING_DEFAULTS[name]
        if name == "conf":
            default = 0.25 if mode in {"predict", "track"} else 0.001
        schema = _schema(name, default)
        status, reason = "configurable", None
        if name in MANAGED:
            status, reason = "managed", MANAGED[name]
        if name in TASK_FIELDS and task not in TASK_FIELDS[name]:
            status, reason = "unavailable", f"仅用于 {', '.join(TASK_FIELDS[name])} 任务"
        unavailable = TRAINING_UNAVAILABLE if mode == "train" else PREDICTION_UNAVAILABLE
        if mode in {"train", "predict"} and name in unavailable:
            status, reason = "unavailable", unavailable[name]
        if mode not in {"train", "predict", "val", "export"}:
            status, reason = "unavailable", "该模式的独立任务将在后续流程阶段接入"
        if mode == "export":
            allowed = {"format", "imgsz", "batch", "dynamic", "simplify", "opset", "quantize", "verbose"}
            if name not in MANAGED and name not in allowed:
                status, reason = "unavailable", "首版导出为 FP32 ONNX、外置后处理；验证参数在验证面板配置"
            if name == "format":
                default, schema = "onnx", {"enum": ["onnx"]}
            elif name == "batch":
                default, schema = 1, {"type": "integer", "minimum": 1, "maximum": 64}
            elif name == "quantize":
                schema = {"enum": [None, 32, "fp32", "w32a32"]}
            elif name == "opset":
                schema = {"anyOf": [{"type": "null"}, {"type": "integer", "minimum": 13, "maximum": 20}]}
        if mode == "val" and name == "dnn":
            status, reason = "unavailable", "当前验证使用 PyTorch 权重；OpenCV DNN 需要 ONNX 后端"
        if name == "imgsz" and mode == "train":
            schema = schema["anyOf"][0]
        if name == "pretrained" and mode == "train":
            schema = {"type": "boolean"}
        if name == "split" and mode == "train":
            schema = {"type": "string", "enum": ["train", "val"]}
        if name == "batch" and mode == "val":
            schema = {"type": "integer", "minimum": 1, "maximum": 65536}
        if name == "fraction" and mode == "val":
            schema = {"type": "number", "exclusiveMinimum": 0, "maximum": 1}
        if name == "max_det" and mode == "predict":
            schema["maximum"] = 10_000  # result protocol's maximum detection count
        if name == "quantize" and mode in {"predict", "val"}:
            schema = {"enum": [None, 16, 32, "fp16", "fp32", "w16a16", "w32a32"]}
        entries[name] = {
            "type": "json", "default": default, "json_schema": schema,
            "title": TITLES.get(name, name),
            "description": (EXPORT_DESCRIPTIONS if mode == "export" else {}).get(
                name, DESCRIPTIONS.get(name, f"{TITLES.get(name, name)}；适用版本 {FRAMEWORK_VERSION}。")),
            "group": _group(name), "common": name in (
                COMMON_TRAINING if mode == "train" else COMMON_VALIDATION if mode == "val" else COMMON_EXPORT if mode == "export" else COMMON_PREDICTION),
            "status": status, "reason": reason,
        }
    common = COMMON_TRAINING if mode == "train" else COMMON_VALIDATION if mode == "val" else COMMON_EXPORT if mode == "export" else COMMON_PREDICTION
    entries = dict(sorted(entries.items(), key=lambda item: (
        common.index(item[0]) if item[0] in common else len(common)
    )))
    body = {"id": CONTRACT_ID, "framework": "ultralytics",
            "framework_version": FRAMEWORK_VERSION, "mode": mode, "task": task,
            "parameters": entries, "default_key_count": _catalog()["default_key_count"]}
    body["sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return body


def normalize_parameters(mode: str, supplied: dict[str, Any]) -> dict[str, Any]:
    json_value(supplied)
    values = deepcopy(supplied)
    aliases = PREDICTION_ALIASES if mode == "predict" else {}
    for old, new in aliases.items():
        if old in values:
            if new in values and values[new] != values[old]:
                raise ValueError(f"conflicting aliases: {old} / {new}")
            values[new] = values.pop(old)
    legacy = {key: values.pop(key) for key in ("half", "int8", "end2end") if key in values}
    if any(type(value) is not bool for value in legacy.values()):
        raise ValueError("half/int8/end2end aliases must be booleans")
    if legacy.get("half") and legacy.get("int8"):
        raise ValueError("half / int8 cannot both be enabled")
    if "half" in legacy or "int8" in legacy:
        precision = 8 if legacy.get("int8") else 16 if legacy.get("half") else None
        aliases = {"int8": 8, "fp16": 16, "fp32": None, "w8a8": 8,
                   "w16a16": 16, "w32a32": None, 32: None}
        given = values.get("quantize")
        given = aliases.get(given, given) if type(given) in (str, int) else given
        if "quantize" in values and given != precision:
            raise ValueError("conflicting aliases: half/int8 / quantize")
        values["quantize"] = precision
    if "end2end" in legacy:
        nms = not legacy["end2end"]
        if "nms" in values and values["nms"] is not nms:
            raise ValueError("conflicting aliases: end2end / nms")
        values["nms"] = nms
    specs = get_contract(mode)["parameters"]
    for name, value in values.items():
        if name not in specs:
            raise ValueError(f"unknown parameter: {name}")
        spec = specs[name]
        if spec["status"] != "configurable":
            raise ValueError(f"{name}: {spec['reason']}")
        validate_value(name, value, spec["json_schema"])
        values[name] = canonical_value(value, spec["json_schema"])
    if isinstance(values.get("scale"), list) and values["scale"][0] > values["scale"][1]:
        raise ValueError("scale: 最小值不能大于最大值")
    if mode == "train":
        values = {**TRAINING_DEFAULTS, **values}
    if mode == "export":
        values = {"format": "onnx", "batch": 1, "imgsz": 640, "dynamic": False,
                  "simplify": True, "opset": None, "quantize": None, **values}
        if not values["dynamic"] and isinstance(values["imgsz"], list) and len(set(values["imgsz"])) > 1:
            raise ValueError("静态 ONNX 对比验证需要方形尺寸；矩形输入请选择动态导出")
    if mode == "val" and values.get("batch", 16) < 1:
        raise ValueError("验证 batch 必须是正整数，不支持 AutoBatch")
    return values


def validate_placement(parameters: dict[str, Any], gpu_count: int) -> None:
    if gpu_count > 1:
        batch = parameters.get("batch", 16)
        if batch < 1 or int(batch) % gpu_count:
            raise ValueError("多卡训练的 batch 必须是 GPU 数量的正整数倍，不支持 AutoBatch")
        if parameters.get("rect"):
            raise ValueError("rect=True 不支持多卡训练，请关闭矩形训练")


def prediction_parameters(image_size: int = 640) -> dict[str, Any]:
    """Build a 1.1 manifest while retaining the five existing parameter names."""
    reverse = {value: key for key, value in PREDICTION_ALIASES.items()}
    result = {}
    for name, spec in get_contract("predict")["parameters"].items():
        if spec["status"] != "configurable":
            continue
        item = {key: deepcopy(spec[key]) for key in (
            "type", "default", "json_schema", "title", "description")}
        if name == "imgsz":
            item["default"] = image_size
        if name == "iou":
            item["default"] = 0.45  # preserve the platform's published-model default
        result[reverse.get(name, name)] = item
    return result


def parameter_warnings(parameters: dict[str, Any]) -> list[str]:
    warnings = []
    if parameters.get("optimizer", "auto") == "auto":
        warnings.append("optimizer=auto：框架自动决定学习率，手填 lr0 不会生效")
    if parameters.get("save") is False:
        warnings.append("save=false：关闭常规 checkpoint 保存，框架仍可能在最后一轮保存；中途停止可能没有权重")
    if parameters.get("val") is False:
        warnings.append("val=false：关闭逐轮验证，框架仍可能执行最终验证或提前停止检查")
    if "dis" in parameters:
        warnings.append("dis：蒸馏损失需要教师模型，当前训练入口尚未接入教师模型选择")
    return warnings
