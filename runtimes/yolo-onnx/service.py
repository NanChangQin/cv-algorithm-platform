import os
from pathlib import Path

from cv_algorithm_sdk import load_manifest
from cv_algorithm_sdk.runtime_profile import RuntimeProfile
from cv_algorithm_sdk.server import create_algorithm_app
from yolo_algorithm.onnx_algorithm import OnnxAlgorithm
from yolo_algorithm.settings import YoloSettings

profile = RuntimeProfile.model_validate_json(os.getenv("CV_RUNTIME_PROFILE", "{}"))
settings = YoloSettings("ultralytics", Path("/models/model/model.onnx"), Path("/data"), "cpu")
app = create_algorithm_app(OnnxAlgorithm(load_manifest("/models/manifest.yaml"), settings, profile))
