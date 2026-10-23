import json

import pytest
from cv_algorithm_sdk.export_artifacts import registered_artifacts, write_manifest
from cv_algorithm_sdk.exports import ExportVerification, verification_parameters
from cv_algorithm_sdk.runtime_contract import normalize_parameters


def test_verification_uses_same_full_dataset_shape_and_float_precision():
    export = normalize_parameters("export", {"imgsz": [64, 96], "batch": 2, "dynamic": True})
    parameters = verification_parameters(
        export, ExportVerification(parameters={"split": "test", "conf": 0.02})
    )
    assert parameters["imgsz"] == [64, 96] and parameters["batch"] == 2
    assert parameters["rect"] is False and parameters["fraction"] == 1
    assert parameters["conf"] == 0.02 and parameters["split"] == "test"
    with pytest.raises(ValueError, match="验证 batch"):
        verification_parameters(export, ExportVerification(parameters={"batch": 1}))


@pytest.mark.parametrize("field", ["augment", "channels_last", "nms"])
def test_backend_specific_verification_options_cannot_skew_comparison(field):
    export = normalize_parameters("export", {})
    with pytest.raises(ValueError, match=f"验证 {field}"):
        verification_parameters(export, ExportVerification(parameters={field: True}))


def test_static_onnx_rejects_rectangular_validation_shape():
    with pytest.raises(ValueError, match="静态 ONNX"):
        normalize_parameters("export", {"imgsz": [64, 96]})


def test_export_directory_manifest_checks_hashes_paths_and_size(tmp_path):
    (tmp_path / "model/data").mkdir(parents=True)
    (tmp_path / "model/model.onnx").write_bytes(b"graph")
    sidecar = tmp_path / "model/data/weights.data"
    sidecar.write_bytes(b"weights")
    write_manifest(tmp_path, ["model/model.onnx", "model/data/weights.data"])
    assert len(registered_artifacts(tmp_path, verify_hash=True)) == 2
    sidecar.write_bytes(b"changed")
    with pytest.raises(ValueError):
        registered_artifacts(tmp_path, verify_hash=True)
    marker = tmp_path / "artifact-manifest.json"
    marker.write_text(json.dumps({"version": 1, "files": {"../outside": {}}}))
    with pytest.raises(ValueError, match="非法路径"):
        registered_artifacts(tmp_path)


def test_unregistered_runtime_files_are_never_exposed(tmp_path):
    (tmp_path / "secret.pt").write_bytes(b"never registered")
    assert registered_artifacts(tmp_path) == {}


def test_non_object_manifest_is_rejected(tmp_path):
    (tmp_path / "artifact-manifest.json").write_text("[]")
    with pytest.raises(ValueError, match="必须是对象"):
        registered_artifacts(tmp_path)
