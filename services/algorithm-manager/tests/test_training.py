import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from algorithm_manager.application.errors import ManagerError
from algorithm_manager.training import (
    MANAGED_TRAINING_LABEL,
    TRAINING_ID_LABEL,
    TrainingService,
    build_training_router,
)

RUN_ID = "11111111-2222-3333-4444-555555555555"
DATASET_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class FakeContainer:
    def __init__(self, labels, kwargs):
        self.labels = labels
        self.kwargs = kwargs
        self.status = "running"
        self.attrs = {"State": {"ExitCode": 0}}
        self.stopped = False
        self.removed = False
        self.log_bytes = b""

    def reload(self):
        return None

    def stop(self, timeout):
        self.stopped = True
        self.status = "exited"
        self.attrs["State"]["ExitCode"] = 143

    def remove(self, force):
        self.removed = True

    def logs(self, tail):
        return self.log_bytes


class FakeContainers:
    def __init__(self):
        self.items = []

    def run(self, image, **kwargs):
        container = FakeContainer(kwargs["labels"], {"image": image, **kwargs})
        self.items.append(container)
        return container

    def list(self, filters, **_):
        wanted = dict(item.split("=", 1) for item in filters["label"])
        return [
            item
            for item in self.items
            if not item.removed and wanted.items() <= item.labels.items()
        ]


class FakeImages:
    def __init__(self, present):
        self.present = present

    def get(self, image):
        if image not in self.present:
            raise RuntimeError("No such image")
        from cv_algorithm_sdk.runtime_contract import CONTRACT_ID
        from cv_algorithm_sdk.training_sources import TRAINING_PROTOCOL

        return SimpleNamespace(
            id="sha256:1",
            labels={
                "cv.platform.parameter-contract": CONTRACT_ID,
                "cv.platform.training-protocol": TRAINING_PROTOCOL,
            },
        )


@pytest.fixture
def docker():
    return SimpleNamespace(
        containers=FakeContainers(), images=FakeImages({"cv-platform/yolo-trainer:dev"})
    )


@pytest.fixture
def service(tmp_path, docker):
    return TrainingService(
        docker,
        tmp_path / "data",
        tmp_path / "host-data",
        gpu_access_mode="runtime",
        max_dataset_bytes=10_000_000,
    )


@pytest.fixture
def client(service):
    app = FastAPI()
    app.include_router(build_training_router(lambda: service))

    @app.exception_handler(ManagerError)
    async def handle(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code})

    return TestClient(app)


def dataset_zip(entries=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in (
            entries
            or {
                "data.yaml": "names: [cat]\n",
                "images/train/a.jpg": "img",
                "labels/train/a.txt": "0 0.5 0.5 0.2 0.2\n",
                "images/val/b.jpg": "img",
            }
        ).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def upload(client, content, sha=None):
    return client.put(
        f"/datasets/{DATASET_ID}",
        content=content,
        headers={"X-Content-SHA256": sha or hashlib.sha256(content).hexdigest()},
    )


def start_payload(**overrides):
    payload = {
        "training_id": RUN_ID,
        "dataset_id": DATASET_ID,
        "image": "cv-platform/yolo-trainer:dev",
        "parameters": {"model": "yolo26n.pt", "epochs": 3, "batch": 8, "imgsz": 640, "lr0": 0.01},
        "gpu_device_ids": ["1"],
    }
    return {**payload, **overrides}


def export_payload(**overrides):
    return start_payload(
        operation="export",
        gpu_device_ids=[],
        parameters={"model": "yolo11n.pt", "format": "onnx", "imgsz": 64},
        **overrides,
    )


def test_export_requires_explicit_image_protocol(client):
    assert upload(client, dataset_zip()).status_code == 200
    response = client.post("/trainings", json=export_payload())
    assert response.status_code == 409
    assert response.json()["code"] == "EXPORT_PROTOCOL_MISMATCH"


def test_export_has_no_gpu_request_and_only_registered_directory_artifacts(
    client, service, docker, monkeypatch
):
    from cv_algorithm_sdk.export_artifacts import write_manifest
    from cv_algorithm_sdk.exports import EXPORT_PROTOCOL

    original = docker.images.get

    def image(name):
        result = original(name)
        result.labels["cv.platform.export-protocol"] = EXPORT_PROTOCOL
        return result

    monkeypatch.setattr(docker.images, "get", image)
    assert upload(client, dataset_zip()).status_code == 200
    response = client.post("/trainings", json=export_payload())
    assert response.status_code == 200, response.text
    options = docker.containers.items[-1].kwargs
    assert "device_requests" not in options and "runtime" not in options
    config = json.loads((service._run_dir(RUN_ID) / "config.json").read_text())
    assert config["device"] == "cpu" and config["export_verification"]["threads"] == 2
    output = service._run_dir(RUN_ID) / "train"
    (output / "model/nested").mkdir(parents=True)
    (output / "model/model.onnx").write_bytes(b"graph")
    (output / "model/nested/weights.data").write_bytes(b"tensor")
    (output / "private.pt").write_bytes(b"not registered")
    names = ["model/model.onnx", "model/nested/weights.data"]
    write_manifest(output, names)
    files = client.get(f"/trainings/{RUN_ID}").json()["files"]
    assert set(files) == {*names, "artifact-manifest.json"}
    assert client.get(f"/trainings/{RUN_ID}/files/model/nested/weights.data").content == b"tensor"
    assert client.get(f"/trainings/{RUN_ID}/files/private.pt").status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"gpu_device_ids": ["0"]},
        {"export_verification": {"parameters": {"imgsz": 96}}},
        {"export_verification": {"iterations": 101}},
    ],
)
def test_export_rejects_gpu_and_conflicting_verification(client, overrides):
    payload = export_payload()
    payload.update(overrides)
    assert client.post("/trainings", json=payload).status_code == 422


def test_weight_transfer_integrity_and_readonly_mount(client, service, docker):
    data = dataset_zip()
    assert upload(client, data).status_code == 200
    content = b"opaque-model"
    digest = hashlib.sha256(content).hexdigest()
    url = f"/training/weights/{digest}"
    assert client.get(url).status_code == 404
    assert client.put(url, content=b"broken").status_code == 400
    assert not service.weight_path(digest).exists()
    assert client.put(url, content=content).status_code == 200
    assert client.get(url).json()["sha256"] == digest
    response = client.post(
        "/trainings",
        json=start_payload(
            training_input={
                "sha256": digest,
                "dataset_sha256": hashlib.sha256(data).hexdigest(),
            }
        ),
    )
    assert response.status_code == 200, response.text
    options = docker.containers.items[-1].kwargs
    assert options["network_mode"] == "none" and options["cap_drop"] == ["ALL"]
    assert options["mounts"][0]["Target"] == "/input/model.pt"
    assert options["mounts"][0]["ReadOnly"] is True
    config = json.loads((service._run_dir(RUN_ID) / "config.json").read_text())
    assert config["training_input"]["sha256"] == digest
    assert config["dataset_sha256"] == hashlib.sha256(data).hexdigest()
    assert client.put("/training/weights/not-a-hash", content=content).status_code == 400


def test_custom_training_rejects_missing_corrupt_and_incompatible_input(client, service, docker):
    data = dataset_zip()
    upload(client, data)
    content = b"weights"
    digest = hashlib.sha256(content).hexdigest()
    payload = start_payload(
        training_input={"sha256": digest, "dataset_sha256": hashlib.sha256(data).hexdigest()}
    )
    assert client.post("/trainings", json=payload).json()["code"] == "WEIGHT_MISSING"
    client.put(f"/training/weights/{digest}", content=content)
    service.weight_path(digest).write_bytes(b"corrupt")
    assert client.post("/trainings", json=payload).json()["code"] == "WEIGHT_MISSING"
    client.put(f"/training/weights/{digest}", content=content)
    payload["training_input"]["dataset_sha256"] = "0" * 64
    assert client.post("/trainings", json=payload).json()["code"] == "DATASET_INVALID"
    assert docker.containers.items == []


def test_independent_validation_uses_val_contract_and_keeps_label_outputs(client, service, docker):
    upload(client, dataset_zip())
    response = client.post(
        "/trainings",
        json=start_payload(
            operation="val",
            parameters={"model": "yolo11n.pt", "split": "test", "batch": 2},
            submitted_parameters={"split": "test", "batch": 2},
        ),
    )
    assert response.status_code == 200, response.text
    config = json.loads((service._run_dir(RUN_ID) / "config.json").read_text())
    assert config["operation"] == "val" and "epochs" not in config
    output = service._run_dir(RUN_ID) / "train"
    (output / "labels").mkdir(parents=True)
    (output / "labels/prediction.txt").write_text("0 .5 .5 .1 .1")
    (output / "notes.txt").write_text("private")
    files = client.get(f"/trainings/{RUN_ID}").json()["files"]
    assert "labels/prediction.txt" in files and "notes.txt" not in files


def test_identical_last_and_best_hashes_still_mount_both_files(client, docker):
    data = dataset_zip()
    upload(client, data)
    digest = hashlib.sha256(b"same").hexdigest()
    client.put(f"/training/weights/{digest}", content=b"same")
    response = client.post(
        "/trainings",
        json=start_payload(
            training_input={
                "sha256": digest,
                "best_sha256": digest,
                "dataset_sha256": hashlib.sha256(data).hexdigest(),
                "resume": True,
                "epoch": 1,
                "epochs": 3,
            }
        ),
    )
    assert response.status_code == 200, response.text
    mounts = docker.containers.items[-1].kwargs["mounts"]
    assert [item["Target"] for item in mounts] == ["/input/model.pt", "/input/best.pt"]
    assert len({item["Source"] for item in mounts}) == 1


def test_dataset_upload_is_verified_and_extracted(client, tmp_path):
    content = dataset_zip()
    assert client.get(f"/datasets/{DATASET_ID}").status_code == 404
    assert upload(client, content, sha="0" * 64).status_code == 400

    response = upload(client, content)
    assert response.status_code == 200
    sha = hashlib.sha256(content).hexdigest()
    assert client.get(f"/datasets/{DATASET_ID}").json()["sha256"] == sha
    root = tmp_path / "data/training/datasets" / DATASET_ID
    assert (root / "labels/train/a.txt").read_text() == "0 0.5 0.5 0.2 0.2\n"
    assert (root / "images/val/b.jpg").stat().st_mode & 0o444 == 0o444
    assert list((tmp_path / "data/training/uploads").iterdir()) == []


@pytest.mark.parametrize("name", ["../escape.txt", "/etc/passwd"])
def test_dataset_paths_cannot_escape(client, tmp_path, name):
    response = upload(client, dataset_zip({name: "x"}))
    assert response.json()["code"] == "DATASET_INVALID"
    assert not (tmp_path / "data/training/escape.txt").exists()
    assert client.get(f"/datasets/{DATASET_ID}").status_code == 404


def test_training_container_is_isolated_and_uses_the_chosen_gpu(client, docker, tmp_path):
    assert client.post("/trainings", json=start_payload()).json()["code"] == "DATASET_MISSING"
    upload(client, dataset_zip())
    missing_image = start_payload(image="cv-platform/other:dev")
    assert client.post("/trainings", json=missing_image).json()["code"] == "IMAGE_MISSING"

    response = client.post("/trainings", json=start_payload())
    assert response.status_code == 200
    assert response.json()["state"] == "running"
    [container] = docker.containers.items
    options = container.kwargs
    assert container.labels == {MANAGED_TRAINING_LABEL: "true", TRAINING_ID_LABEL: RUN_ID}
    assert options["network_mode"] == "none"
    assert options["user"] == "10001:10001"
    assert options["cap_drop"] == ["ALL"]
    assert options["runtime"] == "nvidia"
    assert options["environment"]["NVIDIA_VISIBLE_DEVICES"] == "1"
    assert options["environment"]["YOLO_CONFIG_DIR"] == "/workspace/.config"
    host = tmp_path / "host-data/training"
    assert options["volumes"] == {
        str(host / "datasets" / DATASET_ID): {"bind": "/dataset", "mode": "ro"},
        str(host / "runs" / RUN_ID): {"bind": "/workspace", "mode": "rw"},
    }
    config = json.loads((tmp_path / "data/training/runs" / RUN_ID / "config.json").read_text())
    # 容器里只看得到分配的那张卡，编号从 0 开始
    assert config["device"] == "0"
    assert config["model"] == "yolo26n.pt" and config["epochs"] == 3

    # 重复提交不会再启动一个容器
    client.post("/trainings", json=start_payload())
    assert len(docker.containers.items) == 1


def test_invalid_training_parameters_are_rejected(client):
    bad = start_payload(
        parameters={"model": "../x.pt", "epochs": 3, "batch": 8, "imgsz": 640, "lr0": 0.01}
    )
    assert client.post("/trainings", json=bad).status_code == 422
    bad = start_payload(image="postgres:17")
    assert client.post("/trainings", json=bad).status_code == 422


def write_run(tmp_path, files):
    run = tmp_path / "data/training/runs" / RUN_ID
    for name, content in files.items():
        path = run / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return run


def test_status_reports_progress_metrics_and_downloadable_files(client, docker, tmp_path):
    upload(client, dataset_zip())
    client.post("/trainings", json=start_payload())
    write_run(
        tmp_path,
        {
            "progress.json": json.dumps({"phase": "train", "epoch": 2, "epochs": 3}),
            "train/results.csv": (
                "                  epoch,         train/box_loss,  metrics/mAP50-95(B)\n"
                "                      1,                  1.25,                  0.1\n"
                "                      2,                   1.1,                 0.25\n"
            ),
            "train/weights/best.pt": "weights",
            "train/weights/last.pt": "weights",
            "train/args.yaml": "epochs: 3",
            "train/notes.txt": "not an artifact",
            "train/deep/er/plot.png": "too deep",
        },
    )
    status = client.get(f"/trainings/{RUN_ID}").json()
    assert status["state"] == "running"
    assert status["progress"]["epoch"] == 2
    assert status["metrics"] == [
        {"epoch": 1, "train/box_loss": 1.25, "metrics/mAP50-95(B)": 0.1},
        {"epoch": 2, "train/box_loss": 1.1, "metrics/mAP50-95(B)": 0.25},
    ]
    assert set(status["files"]) == {
        "weights/best.pt",
        "weights/last.pt",
        "args.yaml",
        "results.csv",
    }
    assert client.get(f"/trainings/{RUN_ID}/files/weights/best.pt").content == b"weights"
    assert client.get(f"/trainings/{RUN_ID}/files/notes.txt").status_code == 404
    assert client.get(f"/trainings/{RUN_ID}/files/../config.json").status_code == 404

    [container] = docker.containers.items
    container.status = "exited"
    write_run(tmp_path, {"status.json": json.dumps({"state": "completed"})})
    assert client.get(f"/trainings/{RUN_ID}").json()["state"] == "completed"


def test_nonfinite_metrics_remain_json_serializable(client, tmp_path):
    upload(client, dataset_zip())
    client.post("/trainings", json=start_payload())
    write_run(tmp_path, {"train/results.csv": "epoch,train/box_loss,map\n1,nan,inf\n"})
    response = client.get(f"/trainings/{RUN_ID}")
    assert response.status_code == 200
    assert response.json()["metrics"] == [{"epoch": 1.0}]


def test_crash_and_stop_are_told_apart(client, docker, tmp_path):
    upload(client, dataset_zip())
    client.post("/trainings", json=start_payload())
    [container] = docker.containers.items
    container.status = "exited"
    container.attrs["State"]["ExitCode"] = 137
    failed = client.get(f"/trainings/{RUN_ID}").json()
    assert failed["state"] == "failed"
    assert "137" in failed["error"]
    write_run(tmp_path, {"status.json": json.dumps({"state": "failed", "error": "显存不足"})})
    assert client.get(f"/trainings/{RUN_ID}").json()["error"] == "显存不足"

    container.status = "running"
    stopped = client.post(f"/trainings/{RUN_ID}/stop").json()
    assert container.stopped
    assert stopped["state"] == "stopped"


def test_logs_keep_the_last_progress_refresh(client, docker):
    upload(client, dataset_zip())
    client.post("/trainings", json=start_payload())
    docker.containers.items[
        0
    ].log_bytes = b"start\n  1/3 10%\r  1/3 50%\r\x1b[K  1/3 100%\n\x1b[34mdone\x1b[0m\n"
    assert client.get(f"/trainings/{RUN_ID}/logs").json() == ["start", "  1/3 100%", "done"]


def test_remove_deletes_container_and_files(client, docker, tmp_path):
    upload(client, dataset_zip())
    client.post("/trainings", json=start_payload())
    assert client.delete(f"/trainings/{RUN_ID}").status_code == 204
    assert docker.containers.items[0].removed
    assert not (tmp_path / "data/training/runs" / RUN_ID).exists()
    assert client.get(f"/trainings/{RUN_ID}").status_code == 404


def test_identifiers_must_be_uuids(client):
    assert client.get("/trainings/..%2F..%2Fetc").status_code in {400, 404}
    assert client.get("/datasets/not-a-uuid").json()["code"] == "INPUT_INVALID"


def test_contract_and_complex_config_are_preserved(client, tmp_path):
    from cv_algorithm_sdk.exports import EXPORT_PROTOCOL
    from cv_algorithm_sdk.runtime_contract import get_contract
    from cv_algorithm_sdk.training_sources import TRAINING_PROTOCOL

    assert client.get("/training/parameter-contract").json() == {
        **get_contract("train"),
        "training_protocol": TRAINING_PROTOCOL,
        "export_protocol": EXPORT_PROTOCOL,
    }
    upload(client, dataset_zip())
    submitted = {"batch": 0.75, "classes": [0], "freeze": [0, "model.1"], "amp": "bf16"}
    response = client.post(
        "/trainings",
        json=start_payload(
            parameters={"model": "yolo26n.pt", **submitted},
            submitted_parameters=submitted,
        ),
    )
    assert response.status_code == 200, response.text
    config = json.loads((tmp_path / "data/training/runs" / RUN_ID / "config.json").read_text())
    assert config["submitted_parameters"] == submitted
    assert config["batch"] == 0.75 and config["freeze"] == [0, "model.1"]
    snapshot = {"submitted": submitted, "effective": {"batch": 4}}
    write_run(
        tmp_path,
        {
            "parameter-resolution.json": json.dumps(snapshot),
            "train/parameter-resolution.json": json.dumps(snapshot),
        },
    )
    status = client.get(f"/trainings/{RUN_ID}").json()
    assert status["progress"]["configuration"] == snapshot
    assert "parameter-resolution.json" in status["files"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"parameter_contract_id": "old"},
        {"unknown": 1},
        {"parameters": {"model": "yolo26n.pt", "epohs": 3}},
        {"parameters": {"model": "yolo26n.pt", "epochs": True}},
        {"parameters": {"model": "yolo26n.pt", "device": "0"}},
        {"submitted_parameters": {"epochs": 99}},
    ],
)
def test_manager_rejects_contract_drift_and_invalid_fields(client, overrides):
    assert client.post("/trainings", json=start_payload(**overrides)).status_code == 422


def test_old_training_image_is_rejected_instead_of_dropping_parameters(client, docker):
    upload(client, dataset_zip())
    docker.images.get = lambda _: SimpleNamespace(id="sha256:old", labels={})
    response = client.post("/trainings", json=start_payload())
    assert response.status_code == 409
    assert response.json()["code"] == "PARAMETER_CONTRACT_MISMATCH"
    assert docker.containers.items == []
