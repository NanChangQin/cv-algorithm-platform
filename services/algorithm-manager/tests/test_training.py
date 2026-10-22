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
            item for item in self.items
            if not item.removed and wanted.items() <= item.labels.items()
        ]


class FakeImages:
    def __init__(self, present):
        self.present = present

    def get(self, image):
        if image not in self.present:
            raise RuntimeError("No such image")
        return SimpleNamespace(id="sha256:1")


@pytest.fixture
def docker():
    return SimpleNamespace(
        containers=FakeContainers(), images=FakeImages({"cv-platform/yolo-trainer:dev"})
    )


@pytest.fixture
def service(tmp_path, docker):
    return TrainingService(
        docker, tmp_path / "data", tmp_path / "host-data",
        gpu_access_mode="runtime", max_dataset_bytes=10_000_000,
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
        for name, content in (entries or {
            "data.yaml": "names: [cat]\n",
            "images/train/a.jpg": "img",
            "labels/train/a.txt": "0 0.5 0.5 0.2 0.2\n",
            "images/val/b.jpg": "img",
        }).items():
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
    bad = start_payload(parameters={"model": "../x.pt", "epochs": 3, "batch": 8,
                                    "imgsz": 640, "lr0": 0.01})
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
    write_run(tmp_path, {
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
    })
    status = client.get(f"/trainings/{RUN_ID}").json()
    assert status["state"] == "running"
    assert status["progress"]["epoch"] == 2
    assert status["metrics"] == [
        {"epoch": 1, "train/box_loss": 1.25, "metrics/mAP50-95(B)": 0.1},
        {"epoch": 2, "train/box_loss": 1.1, "metrics/mAP50-95(B)": 0.25},
    ]
    assert set(status["files"]) == {
        "weights/best.pt", "weights/last.pt", "args.yaml", "results.csv",
    }
    assert client.get(f"/trainings/{RUN_ID}/files/weights/best.pt").content == b"weights"
    assert client.get(f"/trainings/{RUN_ID}/files/notes.txt").status_code == 404
    assert client.get(f"/trainings/{RUN_ID}/files/../config.json").status_code == 404

    [container] = docker.containers.items
    container.status = "exited"
    write_run(tmp_path, {"status.json": json.dumps({"state": "completed"})})
    assert client.get(f"/trainings/{RUN_ID}").json()["state"] == "completed"


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
    docker.containers.items[0].log_bytes = b"start\n  1/3 10%\r  1/3 50%\r  1/3 100%\ndone\n"
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
