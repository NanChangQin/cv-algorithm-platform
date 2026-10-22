from pathlib import Path

from cv_algorithm_sdk import load_manifest
from cv_algorithm_sdk.server import create_algorithm_app
from yolo_algorithm.algorithm import YoloAlgorithm
from yolo_algorithm.settings import YoloSettings

BASE_DIR = Path(__file__).resolve().parent
settings = YoloSettings.from_environment()
# 训练发布的模型版本在权重旁边放了自己的 manifest（模型 ID、版本、默认推理尺寸）
published = settings.weights_path.with_name("manifest.yaml")
manifest = load_manifest(published if published.is_file() else BASE_DIR / "manifest.yaml")
app = create_algorithm_app(YoloAlgorithm(manifest, settings))
