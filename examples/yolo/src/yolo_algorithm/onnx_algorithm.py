"""Pinned Ultralytics preprocessing/NMS with an explicitly configured ORT CPU session."""

from contextlib import contextmanager
from time import perf_counter

from cv_algorithm_sdk.export_artifacts import registered_artifacts
from cv_algorithm_sdk.runtime_profile import RuntimeProfile

from .algorithm import YoloAlgorithm


@contextmanager
def cpu_sessions(ort, threads):
    # Startup runs before requests are accepted. Restore immediately afterwards.
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


class OnnxAlgorithm(YoloAlgorithm):
    def __init__(self, manifest, settings, profile: RuntimeProfile):
        super().__init__(manifest, settings)
        self._profile = profile
        self.readiness = {}

    def load(self):
        import numpy as np
        import onnxruntime as ort
        import torch
        from ultralytics.models.yolo.detect import DetectionPredictor

        class ObservedPredictor(DetectionPredictor):
            def preprocess(self, images):
                tensor = super().preprocess(images)
                self.input_tensor_shape = list(tensor.shape)
                return tensor

        if self._settings.device != "cpu" or self._settings.mode != "ultralytics":
            raise ValueError("ONNX 适配器仅支持 ORT CPU 实际推理")
        root = self._settings.weights_path.parent.parent
        entries = registered_artifacts(root, verify_hash=True)
        if "model/model.onnx" not in entries:
            raise ValueError("缺少模型产物校验清单")
        self._profile.resolve(self.manifest, {})
        torch.set_num_threads(self._profile.threads)
        super().load()
        parameters = self.manifest.resolve_parameters(self._profile.defaults)
        from cv_algorithm_sdk.runtime_contract import normalize_parameters

        canonical = normalize_parameters("predict", parameters)
        started = perf_counter()
        with cpu_sessions(ort, self._profile.threads):
            for _ in range(self._profile.warmup_runs):
                outputs = self._model.predict(
                    source=np.zeros((64, 64, 3), dtype=np.uint8),
                    **canonical, predictor=ObservedPredictor,
                    rect=False, device="cpu", verbose=False)
                if len(outputs) != 1 or not torch.isfinite(outputs[0].boxes.data).all():
                    raise ValueError("ORT 冒烟推理输出不合法")
        backend = self._model.predictor.model
        session = backend.session
        shape = session.get_inputs()[0].shape
        if (len(shape) != 4 or shape[1] != 3
            or (isinstance(shape[0], int) and shape[0] != 1)
            or session.get_inputs()[0].type != "tensor(float)"
            or session.get_providers() != ["CPUExecutionProvider"]):
            raise ValueError("ORT 实际输入或 provider 不符合 CPU FP32 batch=1 契约")
        self.readiness = {
            "backend": "onnxruntime", "smoke_passed": True,
            "sample": "synthetic_rgb_64x64", "warmup_runs": self._profile.warmup_runs,
            "warmup_ms": round((perf_counter() - started) * 1000, 3),
            "threads": session.get_session_options().intra_op_num_threads,
            "providers": session.get_providers(), "input_shape": shape,
            "warmup_input_shape": self._model.predictor.input_tensor_shape,
            "artifact_sha256": entries["model/model.onnx"]["sha256"],
            "onnxruntime_version": ort.__version__,
            "defaults": parameters, "allowed_overrides": self._profile.allowed_overrides,
        }

    def predict(self, request):
        parameters = self._profile.resolve(self.manifest, request.parameters)
        result = super().predict(request.model_copy(update={"parameters": parameters}))
        return result
