"""Run only with an isolated memory Backend and the loopback regression node."""

import io
import json
import os
import time
import zipfile
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

assert os.environ.get("CV_PLATFORM_PERSISTENCE_BACKEND") == "memory"
assert os.environ.get("CV_PLATFORM_ALGORITHM_MANAGER_URL") == "http://127.0.0.1:18011/api/v1"

from cv_platform.dependencies import get_container
from cv_platform.main import create_app


class NeverLeader:
    def held(self):
        return False

    def release(self):
        return None


def verify():
    container = get_container()
    object.__setattr__(container, "coordinator_lock", NeverLeader())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data.yaml", "names: [box]\n")
        for split in ("train", "val", "test"):
            for index in range(4):
                canvas = Image.new("RGB", (64, 64), (index * 20, 20, 30))
                ImageDraw.Draw(canvas).rectangle((16, 16, 48, 48), fill=(220, index * 15, 100))
                image = io.BytesIO()
                canvas.save(image, format="PNG")
                archive.writestr(f"images/{split}/{index}.png", image.getvalue())
                archive.writestr(f"labels/{split}/{index}.txt", "0 0.5 0.5 0.5 0.5\n")
    with TestClient(create_app()) as client:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "ChangeMe-Local-123!"})
        assert login.status_code == 200
        response = client.post("/api/v1/training/datasets", files={"file": ("fixture.zip", buffer.getvalue())})
        assert response.status_code == 201, response.text
        dataset = response.json()
        with Path("/fixture.pt").open("rb") as weight:
            response = client.post("/api/v1/training/weights", files={"file": ("fixture.pt", weight)})
        assert response.status_code == 201, response.text
        source = response.json()
        response = client.post("/api/v1/exports", json={
            "name": "isolated API to real Docker node", "node_id": "local", "dataset_id": dataset["id"],
            "source": {"kind": "upload", "reference": source["id"]},
            "parameters": {"imgsz": 64, "opset": 17, "simplify": True},
            "verification": {"parameters": {"split": "test"}, "iterations": 2, "warmup": 1, "threads": 2},
        })
        assert response.status_code == 201, response.text
        job = response.json()
        container.training_orchestrator.prepare(UUID(job["id"]))
        for _ in range(300):
            container.training_orchestrator.sync(UUID(job["id"]))
            job = client.get(f"/api/v1/exports/{job['id']}").json()
            if job["status"] in {"completed", "failed", "stopped"}:
                break
            time.sleep(1)
        assert job["status"] == "completed", job
        report = client.get(f"/api/v1/exports/{job['id']}/report").json()
        assert report["quality_gate"]["passed"] and report["smoke_test"]["passed"]
        assert report["dataset_sha256"] == dataset["sha256"]
        assert report["source_sha256"] == source["sha256"]
        graph = client.get(f"/api/v1/exports/{job['id']}/artifacts/{report['artifact']}")
        assert graph.status_code == 200 and len(graph.content) > 1000
        archive = client.get(f"/api/v1/exports/{job['id']}/archive")
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            assert report["artifact"] in bundle.namelist()
        assert not client.get("/api/v1/training/runs").json()
        node = container.algorithm_manager.node_client("local")
        assert node.request("GET", f"/trainings/{job['id']}", allow_missing=True) is None
        evidence = Path("/evidence")
        evidence.mkdir(exist_ok=True)
        (evidence / "API-VERIFIED.json").write_text(json.dumps({"job": job, "report": report}, indent=2))
        print("API → snapshot → real node → isolated ONNX export → report/artifact collection → node cleanup PASSED", flush=True)


if __name__ == "__main__":
    verify()
