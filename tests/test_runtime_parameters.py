"""Exercise real adapters with a lightweight fake framework; no GPU required."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from cv_algorithm_sdk import AlgorithmManifest, PredictRequest, load_manifest
from cv_algorithm_sdk.runtime_contract import (
    CONTRACT_ID,
    FRAMEWORK_VERSION,
    get_contract,
)

ROOT = Path(__file__).parents[1]


def test_runner_preserves_optional_test_split(runner):
    import yaml

    dataset = runner.WORKSPACE / "dataset"
    (dataset / "images/test").mkdir(parents=True)
    data = yaml.safe_load(runner.prepare_data(dataset).read_text(encoding="utf-8"))
    assert data["test"] == "images/test"
    assert data["val"] == "images/val"
    assert data["train"] == "images/train"


@pytest.fixture
def runner(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "training_runner", ROOT / "runtimes/yolo-trainer/train.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.WORKSPACE = tmp_path
    module.WEIGHTS = tmp_path / "weights"
    module.WEIGHTS.mkdir()
    (module.WEIGHTS / "yolo26n.pt").write_bytes(b"fake")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "data.yaml").write_text("names: [cat, dog]", encoding="utf-8")
    monkeypatch.setenv("YOLO_CONFIG_DIR", str(tmp_path / ".config"))
    framework = ModuleType("ultralytics")
    framework.__version__ = FRAMEWORK_VERSION
    framework.utils = ModuleType("ultralytics.utils")
    framework.utils.SETTINGS = SimpleNamespace(update=lambda **kwargs: None)
    module.calls = []

    class FakeYOLO:
        def __init__(self, *args, **kwargs):
            self.callbacks = {}
            self.model = SimpleNamespace(yaml={"nc": 2, "backbone": []})

        def add_callback(self, event, callback):
            self.callbacks[event] = callback

        def train(self, **kwargs):
            module.calls.append(kwargs)
            trainer = SimpleNamespace(
                args=SimpleNamespace(**kwargs),
                batch_size=6,
                epoch=0,
                epochs=3,
                train_loader=[1],
                optimizer=type(
                    "AdamW", (), {"param_groups": [{"lr": 0.002, "initial_lr": 0.003}]}
                )(),
            )
            for event in (
                "on_train_start",
                "on_train_epoch_start",
                "on_train_epoch_end",
                "on_fit_epoch_end",
            ):
                self.callbacks[event](trainer)

    framework.YOLO = FakeYOLO
    monkeypatch.setitem(sys.modules, "ultralytics", framework)
    monkeypatch.setitem(sys.modules, "ultralytics.utils", framework.utils)
    module.framework = framework
    module.config = {
        "model": "yolo26n.pt",
        "dataset": str(dataset),
        "device": "0",
        "epochs": 3,
        "parameter_contract_id": CONTRACT_ID,
    }
    return module


def test_every_configurable_training_parameter_reaches_model(runner, tmp_path):
    values = {
        name: spec["default"]
        for name, spec in get_contract("train")["parameters"].items()
        if spec["status"] == "configurable"
    }
    values.update(
        epochs=3,
        batch=0.75,
        classes=[0],
        freeze=[0, "model.1"],
        amp="bf16",
        fraction=[0.8, 0.5, 0],
        time=None,
        cache="disk",
        pretrained=False,
    )
    runner.train({**runner.config, **values, "submitted_parameters": values})
    [actual] = runner.calls
    assert {key: actual[key] for key in values} == values
    assert actual["device"] == "0" and actual["name"] == "train"
    assert (tmp_path / "cached-dataset/data.yaml").exists()
    assert (tmp_path / "model.yaml").exists()
    snapshot = json.loads(
        (tmp_path / "train/parameter-resolution.json").read_text(encoding="utf-8")
    )
    assert snapshot["submitted"] == values
    assert snapshot["effective"]["batch"] == 6
    assert snapshot["effective"]["optimizer"] == "AdamW"
    assert snapshot["effective"]["lr0"] == 0.003
    assert snapshot["adjustments"]["batch"] == {"requested": 0.75, "effective": 6}
    assert any("optimizer=auto" in text for text in snapshot["warnings"])


def test_empty_submission_survives_readback(runner, tmp_path):
    runner.train({**runner.config, "submitted_parameters": {}})
    snapshot = json.loads((tmp_path / "parameter-resolution.json").read_text(encoding="utf-8"))
    assert snapshot["submitted"] == {}


def test_full_checkpoint_survives_optimizer_stripping(runner, tmp_path):
    import hashlib

    target = tmp_path / "train/weights/last.pt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"full-optimizer-scaler-ema-state")
    trainer = SimpleNamespace(
        last=target,
        epoch=1,
        epochs=5,
        args=SimpleNamespace(time=None),
        optimizer=type("AdamW", (), {"param_groups": [{"lr": 0.001, "initial_lr": 0.002}]})(),
    )
    runner.preserve_checkpoint(trainer, {"dataset_sha256": "a" * 64})
    target.write_bytes(b"stripped-inference-weights")
    saved = tmp_path / "train/weights/resume-000002.pt"
    assert saved.read_bytes() == b"full-optimizer-scaler-ema-state"
    metadata = json.loads((tmp_path / "train/checkpoint.json").read_text(encoding="utf-8"))
    assert metadata["resumable"] and metadata["epoch"] == 2
    assert metadata["sha256"] == hashlib.sha256(saved.read_bytes()).hexdigest()
    assert metadata["optimizer"] == "AdamW" and metadata["lr0"] == 0.002
    trainer.epoch = 4
    runner.preserve_checkpoint(trainer, {})
    assert not json.loads((tmp_path / "train/checkpoint.json").read_text(encoding="utf-8"))[
        "resumable"
    ]


def test_interrupted_checkpoint_copy_keeps_previous_generation(runner, tmp_path, monkeypatch):
    target = tmp_path / "train/weights/last.pt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"first-epoch")
    trainer = SimpleNamespace(last=target, epoch=0, epochs=5, args=SimpleNamespace(time=None))
    runner.preserve_checkpoint(trainer, {})
    original = (tmp_path / "train/checkpoint.json").read_text()
    trainer.epoch = 1
    monkeypatch.setattr(
        runner.shutil, "copyfile", lambda *_: (_ for _ in ()).throw(OSError("interrupted"))
    )
    with pytest.raises(OSError):
        runner.preserve_checkpoint(trainer, {})
    assert (tmp_path / "train/checkpoint.json").read_text() == original
    assert (tmp_path / "train/weights/resume-000001.pt").read_bytes() == b"first-epoch"


def test_resume_pins_resolved_auto_optimizer_and_remaps_paths(runner, tmp_path, monkeypatch):
    class DetectionTrainer:
        def check_resume(self, overrides):
            self.args = SimpleNamespace(
                epochs=8, optimizer="auto", lr0=0.01, data="/old/dataset.yaml"
            )

    detect = ModuleType("ultralytics.models.yolo.detect")
    detect.DetectionTrainer = DetectionTrainer
    monkeypatch.setitem(sys.modules, "ultralytics.models.yolo.detect", detect)
    source = {"epochs": 8, "optimizer": "AdamW", "lr0": 0.002}
    trainer = runner.resume_trainer({}, {"data": "/dataset/data.yaml"}, source)()
    trainer.check_resume({"batch": 4})
    assert trainer.args.optimizer == "AdamW" and trainer.args.lr0 == 0.002
    assert trainer.args.data == "/dataset/data.yaml"
    assert trainer.args.project == str(tmp_path)
    assert trainer.args.save_dir == str(tmp_path / "train")
    assert trainer.args.name == "train" and trainer.args.exist_ok
    source["epochs"] = 9
    with pytest.raises(ValueError, match="checkpoint 计划轮数不一致"):
        trainer.check_resume({})


def test_runtime_checks_input_hash_before_framework_load(runner, tmp_path):
    runner.INPUT_WEIGHT = tmp_path / "custom.pt"
    runner.INPUT_WEIGHT.write_bytes(b"not-the-expected-input")
    with pytest.raises(ValueError, match="SHA256"):
        runner.train({**runner.config, "training_input": {"sha256": "0" * 64}})


def test_runtime_rejects_unknown_or_mismatched_contract(runner, tmp_path):
    for extra in ({"epohs": 3}, {"parameter_contract_id": "old"}):
        with pytest.raises(ValueError):
            runner.training_arguments({**runner.config, **extra}, tmp_path / "data.yaml")
    runner.framework.__version__ = "old"
    with pytest.raises(ValueError, match="不匹配"):
        runner.train(runner.config)


def test_prediction_forwards_complex_configuration_and_reports_effective(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "examples/yolo/src"))
    from yolo_algorithm.algorithm import YoloAlgorithm
    from yolo_algorithm.settings import YoloSettings

    manifest = load_manifest(ROOT / "examples/yolo/manifest.yaml")
    # Serialized 1.1 packages must remain reloadable by the SDK.
    assert AlgorithmManifest.model_validate_json(manifest.model_dump_json()) == manifest
    image = tmp_path / "image.jpg"
    image.write_bytes(b"image")
    adapter = YoloAlgorithm(
        manifest, YoloSettings("ultralytics", tmp_path / "model.pt", tmp_path, "0")
    )
    calls = []

    class Model:
        def predict(self, **kwargs):
            calls.append(kwargs)
            self.predictor = SimpleNamespace(args=SimpleNamespace(**kwargs))
            return [SimpleNamespace(speed={}, boxes=[], orig_shape=(720, 1280))]

    adapter._model = Model()
    monkeypatch.setattr(adapter, "_resolve_asset", lambda _: image)
    submitted = {"image_size": [640, 960], "classes": [0, 1], "confidence": 0, "quantize": "fp16"}
    result = adapter.predict(
        PredictRequest.model_validate(
            {
                "request_id": "complex",
                "input": {"asset_uri": str(image)},
                "parameters": submitted,
            }
        )
    )
    assert len(calls[0]) == 15  # 12 configurable arguments plus source/device/verbose
    assert calls[0]["imgsz"] == [640, 960] and calls[0]["conf"] == 0
    assert calls[0]["classes"] == [0, 1]
    assert result.configuration["submitted"] == submitted
    assert result.configuration["effective"]["quantize"] == "fp16"
    assert result.configuration["normalized"]["iou"] == 0.45


@pytest.mark.parametrize("size,checked", [(96, [96, 96]), ([64, 96], [64, 96])])
def test_prediction_reports_checked_size_and_actual_tensor_shape(size, checked, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "examples/yolo/src"))
    from yolo_algorithm.algorithm import YoloAlgorithm
    from yolo_algorithm.settings import YoloSettings

    adapter = YoloAlgorithm(
        load_manifest(ROOT / "examples/yolo/manifest.yaml"),
        YoloSettings("ultralytics", tmp_path / "model.onnx", tmp_path, "cpu"),
    )

    class Model:
        def predict(self, **kwargs):
            self.predictor = SimpleNamespace(
                args=SimpleNamespace(**kwargs), imgsz=checked,
                input_tensor_shape=[1, 3, *checked],
            )
            return [SimpleNamespace(speed={}, boxes=[], orig_shape=(64, 64))]

    adapter._model = Model()
    monkeypatch.setattr(adapter, "_resolve_asset", lambda _: tmp_path / "image.jpg")
    result = adapter.predict(PredictRequest.model_validate({
        "request_id": "size", "input": {"asset_uri": "image.jpg"},
        "parameters": {"image_size": size},
    }))
    assert result.configuration["submitted"]["image_size"] == size
    assert result.configuration["normalized"]["imgsz"] == size
    assert result.configuration["effective"]["imgsz"] == checked
    assert result.configuration["input_tensor_shape"] == [1, 3, *checked]
