import io
import zipfile
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient

from cv_platform.core.errors import ApplicationError
from cv_platform.dependencies import get_container
from cv_platform.modules.training.domain import TrainingStatus

RESULTS = [
    {"epoch": 1, "train/box_loss": 1.4, "metrics/precision(B)": 0.5,
     "metrics/recall(B)": 0.4, "metrics/mAP50(B)": 0.45, "metrics/mAP50-95(B)": 0.2},
    {"epoch": 2, "train/box_loss": 1.1, "metrics/precision(B)": 0.7,
     "metrics/recall(B)": 0.6, "metrics/mAP50(B)": 0.66, "metrics/mAP50-95(B)": 0.38},
    {"epoch": 3, "train/box_loss": 1.0, "metrics/precision(B)": 0.68,
     "metrics/recall(B)": 0.61, "metrics/mAP50(B)": 0.64, "metrics/mAP50-95(B)": 0.36},
]


def make_zip(entries: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def ultralytics_layout() -> dict[str, str]:
    return {
        "data.yaml": "path: .\ntrain: images/train\nval: images/val\nnames: [cat, dog]\n",
        "images/train/a.jpg": "a",
        "labels/train/a.txt": "0 0.5 0.5 0.2 0.2\n1 0.3 0.3 0.1 0.1\n",
        "images/train/b.jpg": "b",
        "labels/train/b.txt": "",
        "images/val/c.jpg": "c",
        "labels/val/c.txt": "1 0.5 0.5 0.4 0.4\n",
    }


def upload_dataset(client: TestClient, entries: dict, name: str = "猫狗") -> dict:
    response = client.post(
        "/api/v1/training/datasets",
        files={"file": ("pets.zip", make_zip(entries), "application/zip")},
        data={"name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def stored_zip(dataset_id: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(get_container().training_storage.path(dataset_id))


def test_ultralytics_layout_is_kept_and_counted(client: TestClient) -> None:
    dataset = upload_dataset(client, ultralytics_layout())
    assert dataset["class_names"] == ["cat", "dog"]
    stats = dataset["stats"]
    assert stats["splits"]["train"] == {"images": 2, "labeled": 1, "backgrounds": 1, "boxes": 2}
    assert stats["splits"]["val"] == {"images": 1, "labeled": 1, "backgrounds": 0, "boxes": 1}
    assert stats["instances"] == {"cat": 1, "dog": 2}
    assert stats["auto_split"] is False
    with stored_zip(dataset["id"]) as archive:
        assert yaml.safe_load(archive.read("data.yaml"))["names"] == ["cat", "dog"]
        assert sorted(archive.namelist()) == [
            "data.yaml", "images/train/a.jpg", "images/train/b.jpg", "images/val/c.jpg",
            "labels/train/a.txt", "labels/val/c.txt",
        ]


def test_roboflow_export_in_a_wrapper_folder_is_normalized(client: TestClient) -> None:
    dataset = upload_dataset(client, {
        "export/data.yaml": "names:\n  0: 安全帽\n  1: 人\n",
        "export/train/images/x.png": "x",
        "export/train/labels/x.txt": "0 0.5 0.5 0.2 0.2\n",
        "export/valid/images/y.jpg": "y",
        "export/valid/labels/y.txt": "1 0.5 0.5 0.2 0.2\n",
        "export/test/images/z.jpg": "z",
        "export/README.roboflow.txt": "ignored",
    })
    assert dataset["class_names"] == ["安全帽", "人"]
    assert dataset["stats"]["splits"]["val"]["images"] == 1
    assert any("测试集" in item for item in dataset["stats"]["warnings"])
    with stored_zip(dataset["id"]) as archive:
        assert "images/train/x.png" in archive.namelist()
        assert archive.read("labels/val/y.txt") == b"1 0.5 0.5 0.2 0.2\n"


def test_coco_style_split_folders_are_recognized(client: TestClient) -> None:
    dataset = upload_dataset(client, {
        "coco/data.yaml": "names: [person]\n",
        "coco/images/train2017/1.jpg": "1",
        "coco/labels/train2017/1.txt": "0 0.5 0.5 0.2 0.2\n",
        "coco/images/val2017/2.jpg": "2",
        "coco/labels/val2017/2.txt": "0 0.5 0.5 0.2 0.2\n",
    })
    assert dataset["stats"]["auto_split"] is False
    assert dataset["stats"]["splits"]["val"]["boxes"] == 1


def test_flat_dataset_is_split_and_bad_lines_are_dropped(client: TestClient) -> None:
    entries = {"classes.txt": "car\ntruck\n"}
    for index in range(30):
        entries[f"images/img{index}.jpg"] = str(index)
        entries[f"labels/img{index}.txt"] = "0 0.5 0.5 0.1 0.1\n1 0.2 0.2 0.1 0.1\n"
    entries["labels/img0.txt"] = "0 0.5 0.5 0.1 0.1\n0 1.7 0.5 0.1 0.1\nhello\n"
    entries["labels/orphan.txt"] = "0 0.5 0.5 0.1 0.1\n"
    entries["README.txt"] = "downloaded from somewhere"
    dataset = upload_dataset(client, entries)
    stats = dataset["stats"]
    assert stats["auto_split"] is True
    splits = stats["splits"]
    assert splits["train"]["images"] + splits["val"]["images"] == 30
    assert 1 <= splits["val"]["images"] < 10
    assert stats["invalid_lines"] == 2
    assert stats["orphan_labels"] == 1
    assert stats["skipped_files"] == 1
    assert any("第 2 行" in item for item in stats["invalid_examples"])
    assert stats["instances"] == {"car": 30, "truck": 29}


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ({"readme.md": "x"}, "没有找到图片"),
        ({"data.yaml": "names: [a]", "images/train/a.jpg": "a", "images/val/b.jpg": "b",
          "labels/train/a.txt": "3 0.5 0.5 0.1 0.1"}, "类别编号 3"),
        ({"data.yaml": "names: [a]", "images/train/a.jpg": "a", "images/val/b.jpg": "b"},
         "没有任何标注框"),
        ({"../evil.jpg": "x"}, "非法路径"),
    ],
)
def test_broken_datasets_are_explained(client: TestClient, entries, message) -> None:
    response = client.post(
        "/api/v1/training/datasets",
        files={"file": ("bad.zip", make_zip(entries), "application/zip")},
    )
    assert response.status_code == 400
    assert message in response.json()["message"]


class FakeNode:
    """模拟训练节点上的 Algorithm Manager。"""

    node_id = "local"
    node_name = "测试服务器"

    def __init__(self) -> None:
        self.datasets: dict[str, str] = {}
        self.uploads = 0
        self.started: list[dict] = []
        self.state: dict = {"state": "running", "progress": None, "metrics": [], "files": {}}
        self.stop_calls = 0
        self.deleted: list[str] = []
        self.has_image = True
        self.gpu_list = [
            {"index": 0, "name": "A", "memory_total_mb": 16000, "memory_used_mb": 9000},
            {"index": 1, "name": "B", "memory_total_mb": 16000, "memory_used_mb": 1000},
        ]

    def gpus(self):
        return self.gpu_list

    def image_exists(self, image):
        return self.has_image

    def upload(self, path, source: Path, *, headers, progress=None):
        self.uploads += 1
        self.datasets[path.rsplit("/", 1)[-1]] = headers["X-Content-SHA256"]
        if progress:
            progress(source.stat().st_size, source.stat().st_size)

    def download(self, path, target: Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        content = f"content of {path.rsplit('/files/', 1)[-1]}".encode()
        target.write_bytes(content)
        return len(content)

    def request(self, method, path, *, json=None, params=None, timeout=None,
                allow_missing=False):
        if method == "GET" and path.startswith("/datasets/"):
            sha = self.datasets.get(path.rsplit("/", 1)[-1])
            return {"sha256": sha} if sha else None
        if method == "POST" and path == "/trainings":
            self.started.append(json)
            return {"state": "running"}
        if method == "POST" and path.endswith("/stop"):
            self.stop_calls += 1
            self.state = {**self.state, "state": "stopped"}
            return self.state
        if method == "GET" and path.endswith("/logs"):
            return ["epoch 1", "epoch 2"]
        if method == "GET" and path.startswith("/trainings/"):
            return self.state
        if method == "DELETE":
            self.deleted.append(path)
            return None
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture
def node(monkeypatch) -> FakeNode:
    fake = FakeNode()
    cluster = get_container().algorithm_manager
    monkeypatch.setattr(cluster, "node_client", lambda node_id: fake)
    monkeypatch.setattr(cluster, "node_clients", lambda: [fake])
    return fake


def create_run(client: TestClient, dataset_id: str, **overrides) -> dict:
    payload = {
        "dataset_id": dataset_id, "model": "yolo26n.pt", "node_id": "local",
        "parameters": {"epochs": 3, "batch": 8},
        **overrides,
    }
    response = client.post("/api/v1/training/runs", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def get_run(client: TestClient, run_id: str) -> dict:
    return client.get(f"/api/v1/training/runs/{run_id}").json()


def test_training_runs_from_upload_to_downloadable_results(client: TestClient, node) -> None:
    dataset = upload_dataset(client, ultralytics_layout())
    run = create_run(client, dataset["id"])
    assert run["status"] == "queued"
    # 未指定 GPU 时选空闲显存最多的卡
    assert run["gpu_device_ids"] == ["1"]
    assert run["name"] == "猫狗 · yolo26n"
    orchestrator = get_container().training_orchestrator
    run_id = UUID(run["id"])

    orchestrator.prepare(run_id)
    current = get_run(client, run["id"])
    assert current["status"] == "running"
    assert current["started_at"] is not None
    assert node.uploads == 1
    [started] = node.started
    assert started["dataset_id"] == dataset["id"]
    assert started["gpu_device_ids"] == ["1"]
    assert started["parameters"]["model"] == "yolo26n.pt"
    assert started["parameters"]["epochs"] == 3 and started["parameters"]["imgsz"] == 640

    node.state = {"state": "running", "progress": {"epoch": 2, "epochs": 3},
                  "metrics": RESULTS[:2], "files": {}}
    orchestrator.sync(run_id)
    current = get_run(client, run["id"])
    assert current["progress"]["epoch"] == 2
    assert current["best"]["epoch"] == 2

    node.state = {"state": "completed", "progress": {"epoch": 3, "epochs": 3},
                  "metrics": RESULTS, "files": {"weights/best.pt": 7, "results.csv": 3}}
    orchestrator.sync(run_id)
    finished = get_run(client, run["id"])
    assert finished["status"] == "completed"
    assert finished["best"] == {"epoch": 2, "precision": 0.7, "recall": 0.6, "map50": 0.66,
                                "map50_95": 0.38}
    assert set(finished["artifacts"]) == {"weights/best.pt", "results.csv"}
    assert node.deleted == [f"/trainings/{run['id']}"]
    assert client.get(f"/api/v1/training/runs/{run['id']}/logs").json() == ["epoch 1", "epoch 2"]

    weights = client.get(f"/api/v1/training/runs/{run['id']}/artifacts/weights/best.pt")
    assert weights.content == b"content of weights/best.pt"
    archive = client.get(f"/api/v1/training/runs/{run['id']}/archive")
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert sorted(bundle.namelist()) == ["results.csv", "weights/best.pt"]
    missing = client.get(f"/api/v1/training/runs/{run['id']}/artifacts/..%2F..%2Fsecret")
    assert missing.status_code == 404

    # 同一数据集再次训练不重复上传
    second = create_run(client, dataset["id"], gpu_device_ids=["0"])
    orchestrator.prepare(UUID(second["id"]))
    assert node.uploads == 1
    assert node.started[-1]["gpu_device_ids"] == ["0"]


def test_stopping_keeps_saved_checkpoints(client: TestClient, node) -> None:
    dataset = upload_dataset(client, ultralytics_layout())
    orchestrator = get_container().training_orchestrator
    queued = create_run(client, dataset["id"])
    stopped = client.post(f"/api/v1/training/runs/{queued['id']}/stop").json()
    assert stopped["status"] == "stopped"
    orchestrator.prepare(UUID(queued["id"]))
    assert node.started == []

    run = create_run(client, dataset["id"])
    orchestrator.prepare(UUID(run["id"]))
    node.state = {"state": "running", "progress": None, "metrics": RESULTS[:1],
                  "files": {"weights/last.pt": 7}}
    stopping = client.post(f"/api/v1/training/runs/{run['id']}/stop").json()
    assert stopping["status"] == "stopping"
    assert node.stop_calls == 1
    assert client.delete(f"/api/v1/training/runs/{run['id']}").status_code == 409

    orchestrator.sync(UUID(run["id"]))
    finished = get_run(client, run["id"])
    assert finished["status"] == "stopped"
    assert finished["error"] is None
    assert finished["artifacts"] == {"weights/last.pt": 26}
    assert client.post(f"/api/v1/training/runs/{run['id']}/stop").status_code == 409


def test_problems_on_the_node_are_reported(client: TestClient, node) -> None:
    dataset = upload_dataset(client, ultralytics_layout())
    orchestrator = get_container().training_orchestrator
    node.has_image = False
    run = create_run(client, dataset["id"])
    orchestrator.prepare(UUID(run["id"]))
    failed = get_run(client, run["id"])
    assert failed["status"] == "failed"
    assert "没有训练镜像" in failed["error"]

    node.has_image = True
    run = create_run(client, dataset["id"])
    orchestrator.prepare(UUID(run["id"]))
    node.state = {"state": "failed", "error": "显存不足，请减小 batch 或图片尺寸后重试",
                  "progress": None, "metrics": [], "files": {}}
    orchestrator.sync(UUID(run["id"]))
    assert get_run(client, run["id"])["error"] == "显存不足，请减小 batch 或图片尺寸后重试"

    run = create_run(client, dataset["id"])
    orchestrator.prepare(UUID(run["id"]))

    def unreachable(*args, **kwargs):
        raise ApplicationError("RUNTIME_NODE_UNAVAILABLE", "无法连接服务器：测试服务器", 503)

    node.request = unreachable
    orchestrator.sync(UUID(run["id"]))
    waiting = get_run(client, run["id"])
    assert waiting["status"] == "running"
    assert waiting["stage"] == "无法连接服务器：测试服务器，稍后重试"


def test_training_requests_are_validated(client: TestClient, node) -> None:
    dataset = upload_dataset(client, ultralytics_layout())
    base = {"dataset_id": dataset["id"], "model": "yolo26n.pt", "node_id": "local"}
    bad_parameters = client.post("/api/v1/training/runs", json={
        **base, "parameters": {"imgsz": 650},
    })
    assert bad_parameters.status_code == 422
    unknown_model = client.post("/api/v1/training/runs", json={**base, "model": "x.pt"})
    assert unknown_model.json()["code"] == "MODEL_INVALID"
    wrong_gpu = client.post("/api/v1/training/runs", json={**base, "gpu_device_ids": ["7"]})
    assert wrong_gpu.json()["code"] == "GPU_UNAVAILABLE"
    node.gpu_list = []
    no_gpu = client.post("/api/v1/training/runs", json=base)
    assert no_gpu.status_code == 409
    assert "没有检测到 GPU" in no_gpu.json()["message"]


def test_datasets_used_by_training_cannot_be_deleted(client: TestClient, node) -> None:
    used = upload_dataset(client, ultralytics_layout())
    unused = upload_dataset(client, ultralytics_layout(), name="备用")
    create_run(client, used["id"])
    assert client.delete(f"/api/v1/training/datasets/{used['id']}").status_code == 409
    assert client.delete(f"/api/v1/training/datasets/{unused['id']}").status_code == 204
    assert node.deleted == [f"/datasets/{unused['id']}"]
    assert not get_container().training_storage.path(unused["id"]).exists()
    names = [item["name"] for item in client.get("/api/v1/training/datasets").json()]
    assert names == ["猫狗"]


def test_coordinator_requeues_preparation_interrupted_by_a_restart(
    client: TestClient, node
) -> None:
    dataset = upload_dataset(client, ultralytics_layout())
    run = create_run(client, dataset["id"])
    container = get_container()
    container.training.update(UUID(run["id"]), status=TrainingStatus.PREPARING)
    orchestrator = container.training_orchestrator
    orchestrator.tick()
    assert get_run(client, run["id"])["status"] == "queued"
    orchestrator.tick()
    orchestrator.shutdown(wait=True)
    assert get_run(client, run["id"])["status"] == "running"


def test_a_start_that_fails_midway_does_not_leave_training_on_the_node(
    client: TestClient, node
) -> None:
    dataset = upload_dataset(client, ultralytics_layout())
    run = create_run(client, dataset["id"])
    original = node.request

    def timeout_on_start(method, path, **kwargs):
        if method == "POST" and path == "/trainings":
            raise ApplicationError("RUNTIME_NODE_UNAVAILABLE", "无法连接服务器：测试服务器", 503)
        return original(method, path, **kwargs)

    node.request = timeout_on_start
    get_container().training_orchestrator.prepare(UUID(run["id"]))
    assert get_run(client, run["id"])["status"] == "failed"
    assert node.deleted == [f"/trainings/{run['id']}"]
