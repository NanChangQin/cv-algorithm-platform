from pathlib import Path
from time import perf_counter
from urllib.parse import unquote, urlparse

from cv_algorithm_sdk import (
    AlgorithmIdentity,
    Detection,
    DetectionAlgorithm,
    ImageMetadata,
    ObjectDetectionData,
    ObjectDetectionResult,
    PredictRequest,
    TimingMetadata,
)
from cv_algorithm_sdk.runtime_contract import (
    CONTRACT_ID,
    FRAMEWORK_VERSION,
    normalize_parameters,
)

from .settings import YoloSettings


class YoloAlgorithm(DetectionAlgorithm):
    def __init__(self, manifest, settings: YoloSettings) -> None:
        super().__init__(manifest)
        self._settings = settings
        self._model = None

    def load(self) -> None:
        if self._settings.mode == "stub":
            return
        if self._settings.mode != "ultralytics":
            raise ValueError("CV_YOLO_MODE must be ultralytics or stub")
        if not self._settings.weights_path.is_file():
            raise FileNotFoundError(f"YOLO weights not found: {self._settings.weights_path}")
        import ultralytics
        from ultralytics import YOLO

        if self.manifest.schema_version == "1.1" and ultralytics.__version__ != FRAMEWORK_VERSION:
            raise ValueError("推理镜像版本与参数契约不一致，请重建 YOLO 推理镜像")

        self._model = YOLO(str(self._settings.weights_path), task="detect")

    def predict(self, request: PredictRequest) -> ObjectDetectionResult:
        parameters = self.manifest.resolve_parameters(request.parameters)
        canonical = normalize_parameters("predict", parameters)
        confidence = float(canonical["conf"])
        if self._settings.mode == "stub":
            return self._stub_result(request, confidence)
        if self._model is None:
            raise RuntimeError("YOLO model is not loaded")
        image_path = self._resolve_asset(request.input.asset_uri)
        started = perf_counter()
        device = None if self._settings.device == "auto" else self._settings.device
        predictions = self._model.predict(
            source=str(image_path),
            **canonical,
            **({"rect": False} if self.manifest.runtime.framework == "onnx" else {}),
            device=device,
            verbose=False,
        )
        finished = perf_counter()
        prediction = predictions[0]
        speed = prediction.speed
        detections = []
        for box in prediction.boxes:
            class_id = int(box.cls.item())
            coordinates = box.xyxy[0].tolist()
            detections.append(
                Detection(
                    label=str(prediction.names[class_id]),
                    score=round(float(box.conf.item()), 6),
                    bbox=[round(float(value), 2) for value in coordinates],
                )
            )
        height, width = prediction.orig_shape
        return ObjectDetectionResult(
            request_id=request.request_id,
            algorithm=AlgorithmIdentity(id=self.manifest.id, version=self.manifest.version),
            input=ImageMetadata(width=width, height=height),
            timing=TimingMetadata(
                preprocess_ms=float(speed.get("preprocess", 0)),
                inference_ms=float(speed.get("inference", (finished - started) * 1000)),
                postprocess_ms=float(speed.get("postprocess", 0)),
            ),
            data=ObjectDetectionData(detections=detections),
            configuration=self._configuration(request.parameters, canonical),
        )

    def _configuration(self, submitted: dict, normalized: dict) -> dict:
        predictor = getattr(self._model, "predictor", None)
        args = getattr(predictor, "args", None)
        effective = {
            key: getattr(args, key) for key in normalized if args is not None and hasattr(args, key)
        }
        # args.imgsz can remain a scalar (dynamic ONNX) or precede stride rounding.
        # The predictor stores its checked [height, width] separately.
        if "imgsz" in normalized and getattr(predictor, "imgsz", None) is not None:
            effective["imgsz"] = list(predictor.imgsz)
        return {
            "contract_id": CONTRACT_ID, "submitted": submitted, "normalized": normalized,
            "effective": effective,
            **({"input_tensor_shape": predictor.input_tensor_shape}
               if getattr(predictor, "input_tensor_shape", None) is not None else {}),
        }

    def _resolve_asset(self, asset_uri: str) -> Path:
        parsed = urlparse(asset_uri)
        if parsed.scheme not in {"", "file"}:
            raise ValueError("only file asset URIs are supported")
        candidate = Path(unquote(parsed.path if parsed.scheme else asset_uri)).resolve()
        root = self._settings.data_root.resolve()
        test_root = Path("/app/test").resolve()
        if (
            candidate != root
            and root not in candidate.parents
            and test_root not in candidate.parents
        ):
            raise ValueError("asset path is outside the mounted data directory")
        if not candidate.is_file():
            raise FileNotFoundError(f"input image not found: {candidate}")
        return candidate

    def _stub_result(self, request: PredictRequest, confidence: float) -> ObjectDetectionResult:
        detections = []
        if confidence <= 0.95:
            detections.append(Detection(label="person", score=0.95, bbox=[105, 76, 438, 684]))
        return ObjectDetectionResult(
            request_id=request.request_id,
            algorithm=AlgorithmIdentity(id=self.manifest.id, version=self.manifest.version),
            input=ImageMetadata(width=1280, height=720),
            timing=TimingMetadata(inference_ms=1),
            data=ObjectDetectionData(detections=detections),
        )
