import io
import json
import zipfile
from uuid import UUID

import pytest
from PIL import Image
from test_training import RESULTS, create_run, ultralytics_layout, upload_dataset

from cv_platform.dependencies import get_container
from cv_platform.modules.training.domain import TrainingStatus


@pytest.mark.parametrize("scale", list("nsmlx"))
@pytest.mark.parametrize("algorithm", ["yolo11", "yolo26"])
def test_yolo_sizes_and_architecture_snapshot(client, node, scale, algorithm):
    dataset = upload_dataset(client, ultralytics_layout())
    model = f"{algorithm}{scale}.pt"
    catalog = client.get("/api/v1/training/models").json()
    assert any(item["key"] == model and item["algorithm"] == algorithm
               and item["scale"] == scale for item in catalog)
    run = create_run(client, dataset["id"], model=model, architecture=model)
    assert run["progress"]["architecture"] == model
    container = get_container()
    container.training_orchestrator.prepare(UUID(run["id"]))
    node.state = {"state": "running", "progress": {"epoch": 1}, "metrics": RESULTS}
    container.training_orchestrator.sync(UUID(run["id"]))
    current = client.get(f"/api/v1/training/runs/{run['id']}").json()
    assert current["progress"]["architecture"] == model
    report = client.get(f"/api/v1/training/runs/{run['id']}/report").json()
    assert report["algorithm"] == algorithm
    assert report["scale"] == scale


def test_incompatible_algorithm_rejected_before_scheduling(client, node):
    dataset = upload_dataset(client, ultralytics_layout())
    response = client.post("/api/v1/training/runs", json={
        "dataset_id": dataset["id"], "node_id": "local", "model": "yolo11n.pt",
        "architecture": "yolo11x.pt",
    })
    assert response.status_code == 422
    assert response.json()["code"] == "MODEL_MISMATCH"
    assert not node.started


def test_report_distinguishes_missing_final_and_best_and_downloads(client, node):
    dataset = upload_dataset(client, ultralytics_layout())
    run = create_run(client, dataset["id"], model="yolo11s.pt", architecture="yolo11s.pt")
    root = f"/api/v1/training/runs/{run['id']}"
    queued = client.get(f"{root}/report").json()
    assert queued["best"] is None and queued["metrics"] == []
    assert any("任务未成功完成" in note for note in queued["notes"])
    container = get_container()
    container.training.update(UUID(run["id"]), status=TrainingStatus.STOPPED,
                              metrics=RESULTS, logs=("测试训练日志",))
    report = client.get(f"{root}/report").json()
    assert report["best"]["epoch"] == 2 and report["last"]["epoch"] == 3
    assert report["evaluation"] is None
    assert report["dataset"]["sha256"] == dataset["sha256"]
    assert report["scale"] == "s"
    for kind in ("metrics.csv", "metrics.json", "report.json", "logs.txt", "per-class.csv"):
        response = client.get(f"{root}/download/{kind}")
        assert response.status_code == 200, response.text
        assert "attachment" in response.headers["content-disposition"]
    metrics = client.get(f"{root}/download/metrics.json").json()
    assert metrics["data"] == report["metrics"]
    png = client.get(f"{root}/charts/metrics")
    image = Image.open(io.BytesIO(png.content))
    assert image.format == "PNG" and image.width > 1000
    assert client.get(f"{root}/charts/unknown").status_code == 404
    assert client.get(f"{root}/figure?name=../../secret.png").status_code == 404
    pdf = client.get(f"{root}/download/report.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    with zipfile.ZipFile(io.BytesIO(client.get(f"{root}/archive").content)) as bundle:
        assert bundle.read("report/report.pdf").startswith(b"%PDF-")
        archived = json.loads(bundle.read("report/report.json"))
        assert archived["metrics"] == report["metrics"]
        assert "测试训练日志" in bundle.read("report/logs.txt").decode()


def test_reports_respect_project_boundary(client, node):
    dataset = upload_dataset(client, ultralytics_layout())
    run = create_run(client, dataset["id"])
    project = client.post("/api/v1/projects", json={"name": "other reports"}).json()
    for suffix in ("report", "download/report.pdf", "charts/metrics", "archive"):
        response = client.get(f"/api/v1/training/runs/{run['id']}/{suffix}",
                              headers={"X-Project-ID": project["id"]})
        assert response.status_code == 404
