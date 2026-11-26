from pathlib import Path

from cv_algorithm_sdk import load_manifest
from cv_algorithm_sdk.server import create_algorithm_app
from faster_rcnn_algorithm.algorithm import FasterRcnnAlgorithm
from faster_rcnn_algorithm.settings import FasterRcnnSettings

BASE_DIR = Path(__file__).resolve().parent
settings = FasterRcnnSettings.from_environment(BASE_DIR)
published = settings.weights_path.with_name("manifest.yaml")
manifest = load_manifest(published if published.is_file() else BASE_DIR / "manifest.yaml")
app = create_algorithm_app(FasterRcnnAlgorithm(manifest, settings))
