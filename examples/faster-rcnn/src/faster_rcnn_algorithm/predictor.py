from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

import yaml
from PIL import Image


@dataclass(frozen=True, slots=True)
class RawDetection:
    label: str
    score: float
    bbox: list[float]


@dataclass(frozen=True, slots=True)
class Prediction:
    width: int
    height: int
    preprocess_ms: float
    inference_ms: float
    postprocess_ms: float
    detections: list[RawDetection]


class Predictor(Protocol):
    def load(self) -> None: ...

    def predict(
        self,
        asset_uri: str,
        confidence: float,
        nms_threshold: float,
        max_detections: int,
        min_box_area: float,
    ) -> Prediction: ...


class StubPredictor:
    """Contract-test backend. It is enabled only through an explicit environment flag."""

    def load(self) -> None:
        return None

    def predict(
        self,
        _: str,
        confidence: float,
        nms_threshold: float,
        max_detections: int,
        min_box_area: float,
    ) -> Prediction:
        detections = []
        if confidence <= 0.92 and min_box_area <= 192_000 and max_detections > 0:
            detections.append(RawDetection("person", 0.92, [110, 80, 430, 680]))
        return Prediction(1280, 720, 0, 1, 0, detections)


class TorchvisionPredictor:
    def __init__(
        self,
        config_path: Path,
        weights_path: Path,
        data_root: Path,
        device: str,
        allow_partial_weights: bool,
    ) -> None:
        self._config_path = config_path
        self._weights_path = weights_path
        self._data_root = data_root.resolve()
        self._requested_device = device
        self._allow_partial_weights = allow_partial_weights
        self._model: Any = None
        self._torch: Any = None
        self._device = "cpu"
        self._labels: dict[int, str] = {}

    def load(self) -> None:
        if not self._config_path.is_file():
            raise FileNotFoundError(f"model config not found: {self._config_path}")
        if not self._weights_path.is_file():
            raise FileNotFoundError(f"model weights not found: {self._weights_path}")

        import torch

        with self._config_path.open("r", encoding="utf-8") as stream:
            config = yaml.safe_load(stream) or {}
        self._device = self._resolve_device(torch)
        if self._device == "cpu":
            torch.set_num_threads(
                max(1, int(os.getenv("CV_FASTER_RCNN_CPU_THREADS", "2")))
            )
        self._model = self._build_model(config.get("model", config))
        state = torch.load(
            self._weights_path, map_location=self._device, weights_only=True
        )
        from cv_algorithm_sdk.faster_rcnn import check_metadata

        check_metadata(state, config)
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
        strict = not self._allow_partial_weights
        self._model.load_state_dict(state, strict=strict)
        self._model.to(self._device)
        self._model.eval()
        self._torch = torch
        if config.get("labels") == "coco":
            from torchvision.models.detection import FasterRCNN_ResNet50_FPN_Weights

            categories = FasterRCNN_ResNet50_FPN_Weights.COCO_V1.meta["categories"]
            self._labels = {
                index: name for index, name in enumerate(categories) if name != "N/A"
            }
        else:
            category_map = config.get("_dataproc", {}).get("category_map", {})
            self._labels = {
                int(category_id): name for name, category_id in category_map.items()
            }

    def predict(
        self,
        asset_uri: str,
        confidence: float,
        nms_threshold: float,
        max_detections: int,
        min_box_area: float,
    ) -> Prediction:
        if self._model is None or self._torch is None:
            raise RuntimeError("predictor is not loaded")
        image_path = self._resolve_asset(asset_uri)

        started = perf_counter()
        image = Image.open(image_path).convert("RGB")
        from torchvision.transforms.functional import pil_to_tensor

        tensor = pil_to_tensor(image).float().div(255).to(self._device)
        preprocess_done = perf_counter()
        with self._torch.inference_mode():
            output = self._model([tensor])
        inference_done = perf_counter()

        predictions = output[1] if isinstance(output, tuple) else output
        prediction = predictions[0]
        from torchvision.ops import batched_nms

        keep = batched_nms(
            prediction["boxes"],
            prediction["scores"],
            prediction["labels"],
            nms_threshold,
        )
        boxes = prediction["boxes"][keep].detach().cpu()
        scores = prediction["scores"][keep].detach().cpu()
        labels = prediction["labels"][keep].detach().cpu()
        detections = []
        for box, score, label in zip(boxes, scores, labels, strict=True):
            area = max(float(box[2] - box[0]), 0) * max(float(box[3] - box[1]), 0)
            if float(score) < confidence or area < min_box_area:
                continue
            detections.append(
                RawDetection(
                    label=self._labels.get(int(label), f"class_{int(label)}"),
                    score=round(float(score), 6),
                    bbox=[round(float(value), 2) for value in box],
                )
            )
            if len(detections) >= max_detections:
                break
        completed = perf_counter()
        return Prediction(
            width=image.width,
            height=image.height,
            preprocess_ms=(preprocess_done - started) * 1000,
            inference_ms=(inference_done - preprocess_done) * 1000,
            postprocess_ms=(completed - inference_done) * 1000,
            detections=detections,
        )

    def _resolve_device(self, torch: Any) -> str:
        if self._requested_device == "auto":
            return "cuda" if torch.cuda.is_available() else "cpu"
        if self._requested_device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available")
        return self._requested_device

    def _resolve_asset(self, asset_uri: str) -> Path:
        parsed = urlparse(asset_uri)
        if parsed.scheme not in {"", "file"}:
            raise ValueError("only file asset URIs are supported by this container")
        raw_path = unquote(parsed.path if parsed.scheme else asset_uri)
        candidate = Path(raw_path).resolve()
        test_root = Path("/app/test").resolve()
        allowed = (
            candidate == self._data_root
            or self._data_root in candidate.parents
            or test_root in candidate.parents
        )
        if not allowed:
            raise ValueError("asset path is outside the mounted data directory")
        if not candidate.is_file():
            raise FileNotFoundError(f"input image not found: {candidate}")
        return candidate

    @staticmethod
    def _build_model(config: dict[str, Any]) -> Any:
        torchvision_model = config.get("torchvision_model")
        if torchvision_model:
            if torchvision_model != "fasterrcnn_resnet50_fpn":
                raise ValueError(f"unsupported torchvision model: {torchvision_model}")
            from cv_algorithm_sdk.faster_rcnn import build_model

            return build_model(config)

        from torch import nn
        from torchvision.models import get_weight
        from torchvision.models.detection import FasterRCNN
        from torchvision.models.detection.backbone_utils import resnet_fpn_backbone
        from torchvision.models.detection.faster_rcnn import TwoMLPHead
        from torchvision.models.detection.rpn import AnchorGenerator, RPNHead
        from torchvision.ops import MultiScaleRoIAlign

        backbone_config = config["backbone"]
        weights_name = backbone_config.get("weights")
        backbone = resnet_fpn_backbone(
            backbone_name=backbone_config["backbone_name"],
            weights=get_weight(weights_name) if weights_name else None,
            trainable_layers=backbone_config.get("trainable_layers", 5),
            returned_layers=backbone_config.get("returned_layers"),
            norm_layer=None if weights_name else nn.BatchNorm2d,
        )
        anchor_config = config["rpn_anchor_generator"]
        anchor_generator = AnchorGenerator(
            sizes=tuple(tuple(item) for item in anchor_config["sizes"]),
            aspect_ratios=tuple(tuple(item) for item in anchor_config["aspect_ratios"]),
        )
        rpn_config = config["rpn_head"]
        rpn_head = RPNHead(
            backbone.out_channels,
            anchor_generator.num_anchors_per_location()[0],
            conv_depth=rpn_config.get("conv_depth", 1),
        )
        pool_config = config["box_roi_pool"]
        returned_layers = backbone_config.get("returned_layers", [1, 2, 3, 4])
        box_roi_pool = MultiScaleRoIAlign(
            featmap_names=[str(index) for index in range(len(returned_layers))],
            output_size=pool_config["output_size"],
            sampling_ratio=pool_config["sampling_ratio"],
        )
        head_config = config["box_head"]
        box_head = TwoMLPHead(
            backbone.out_channels * box_roi_pool.output_size[0] ** 2,
            head_config["representation_size"],
        )
        ignored = {
            "type",
            "pretrain",
            "backbone",
            "rpn_anchor_generator",
            "rpn_head",
            "box_roi_pool",
            "box_head",
        }
        model_kwargs = {
            key: value for key, value in config.items() if key not in ignored
        }
        return FasterRCNN(
            backbone,
            rpn_anchor_generator=anchor_generator,
            rpn_head=rpn_head,
            box_roi_pool=box_roi_pool,
            box_head=box_head,
            **model_kwargs,
        )
