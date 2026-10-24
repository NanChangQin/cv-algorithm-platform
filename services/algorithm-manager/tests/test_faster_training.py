from types import SimpleNamespace

from cv_algorithm_sdk.training_contract import FASTER_CONTRACT, FASTER_MODEL, FASTER_PROTOCOL
from test_training import DATASET_ID, RUN_ID, client, dataset_zip, docker, service, upload  # noqa: F401


def test_faster_cpu_runtime_uses_no_gpu_and_checks_contract(client, docker, service):  # noqa: F811
    labels = {
        "cv.platform.parameter-contract": FASTER_CONTRACT,
        "cv.platform.training-protocol": FASTER_PROTOCOL,
        "cv.platform.model-selection": "v1",
    }
    docker.images.get = lambda _: SimpleNamespace(labels=labels)
    assert upload(client, dataset_zip()).status_code == 200
    payload = {
        "training_id": RUN_ID,
        "dataset_id": DATASET_ID,
        "image": "cv-platform/faster-rcnn-trainer:dev",
        "parameters": {"model": FASTER_MODEL, "device": "cpu", "epochs": 1},
        "parameter_contract_id": FASTER_CONTRACT,
    }
    response = client.post("/trainings", json=payload)
    assert response.status_code in (200, 201), response.text
    options = docker.containers.items[-1].kwargs
    assert not options.get("device_requests") and options.get("runtime") is None
    import json

    config = json.loads((service._run_dir(RUN_ID) / "config.json").read_text())
    assert config["device"] == "cpu" and config["model"] == FASTER_MODEL
    contract = client.get("/training/parameter-contract", params={"model": FASTER_MODEL}).json()
    assert contract["training_protocol"] == FASTER_PROTOCOL
    assert (
        client.get(
            "/training/parameter-contract", params={"model": FASTER_MODEL, "mode": "export"}
        ).status_code
        == 422
    )
    assert client.post("/trainings", json={**payload, "gpu_device_ids": ["0"]}).status_code == 422
    assert (
        client.post("/trainings", json={**payload, "parameter_contract_id": "wrong"}).status_code
        == 422
    )


def test_faster_validation_cpu_and_cross_family_weights_are_rejected():
    import pytest
    from cv_algorithm_sdk.training_sources import TrainingInput
    from pydantic import ValidationError

    from algorithm_manager.training import StartTrainingRequest

    base = {
        "training_id": RUN_ID,
        "dataset_id": DATASET_ID,
        "image": "cv-platform/faster-rcnn-trainer:dev",
        "parameter_contract_id": FASTER_CONTRACT,
    }
    request = StartTrainingRequest(
        **base, operation="val", parameters={"model": FASTER_MODEL, "device": "cpu"}
    )
    assert request.parameters["split"] == "val"
    with pytest.raises(ValidationError):
        TrainingInput(
            sha256="a" * 64, dataset_sha256="b" * 64, expected_model="fasterrcnn_resnet50_fpn_v2.pt"
        )
    with pytest.raises(ValidationError):
        StartTrainingRequest(**base, parameters={"model": FASTER_MODEL, "mosaic": 1})

    with pytest.raises(ValidationError, match="独立验证不能使用 resume"):
        StartTrainingRequest(
            **base,
            operation="val",
            parameters={"model": FASTER_MODEL, "device": "cpu"},
            training_input={
                "sha256": "a" * 64,
                "dataset_sha256": "b" * 64,
                "expected_model": FASTER_MODEL,
                "resume": True,
            },
        )
