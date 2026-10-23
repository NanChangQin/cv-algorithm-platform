"""Actual publish -> deploy -> ORT image -> exact-instance task regression.

Run inside the isolated Docker network with the disposable Manager. The fixture is
the already verified export directory from verify_onnx_export.py, not a fake graph.
"""

import io
import json
import os
import shutil
import time
import zipfile
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import httpx
from cv_platform.dependencies import get_container
from cv_platform.main import create_app
from cv_platform.modules.scheduling.cluster_gateway import RuntimeNode
from cv_platform.modules.training.domain import TrainingRun, TrainingStatus
from fastapi.testclient import TestClient
from PIL import Image

assert os.environ["CV_PLATFORM_PERSISTENCE_BACKEND"] == "memory"
assert (
    os.environ["CV_PLATFORM_ALGORITHM_MANAGER_URL"]
    == "http://regression-manager:8010/api/v1"
)


class NeverLeader:
    def held(self):
        return False

    def release(self):
        pass


def verify():
    container = get_container()
    object.__setattr__(container, "coordinator_lock", NeverLeader())
    manager = container.algorithm_manager
    manager.register_node(
        RuntimeNode(
            "cpu-regression",
            "Disposable CPU",
            os.environ["CV_PLATFORM_ALGORITHM_MANAGER_URL"],
        )
    )
    deployments = []
    evidence = {"deployments": [], "predictions": [], "docker": []}
    with TestClient(create_app()) as client:
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "ChangeMe-Local-123!"},
            ).status_code
            == 200
        )
        image = io.BytesIO()
        Image.new("RGB", (64, 64), (128, 32, 64)).save(image, format="PNG")
        dataset_zip = io.BytesIO()
        with zipfile.ZipFile(dataset_zip, "w") as bundle:
            bundle.writestr("data.yaml", "names: [box]\n")
            for split in ("train", "val", "test"):
                bundle.writestr(f"images/{split}/a.png", image.getvalue())
                bundle.writestr(f"labels/{split}/a.txt", "0 0.5 0.5 0.5 0.5\n")
        response = client.post(
            "/api/v1/training/datasets",
            files={"file": ("dataset.zip", dataset_zip.getvalue())},
        )
        assert response.status_code == 201, response.text
        dataset = container.training.get_dataset(UUID(response.json()["id"]))
        record = TrainingRun.queued(
            dataset.project_id,
            "verified export fixture",
            dataset.id,
            "custom.pt",
            {},
            "cpu-regression",
            (),
            "admin",
        )
        root = container.training_storage.run_directory(record.id)
        shutil.copytree(Path("/fixture"), root, dirs_exist_ok=True)
        record = replace(
            record,
            status=TrainingStatus.COMPLETED,
            progress={
                "operation": "export",
                "dataset": dataset.snapshot(),
                "dynamic_fixture": json.loads(
                    (root / "export-report.json").read_text()
                )["dynamic"],
            },
            artifacts={
                p.relative_to(root).as_posix(): p.stat().st_size
                for p in root.rglob("*")
                if p.is_file()
            },
        )
        container.training.add_run(record)
        response = client.post(
            f"/api/v1/exports/{record.id}/publish", json={"key": "runtime-regression"}
        )
        assert response.status_code == 201, response.text
        model = response.json()
        assert client.delete(f"/api/v1/exports/{record.id}").status_code == 204
        try:
            for confidence in (0.2, 0.6):
                response = client.post(
                    "/api/v1/deployments",
                    json={
                        "algorithm_version_id": model["id"],
                        "node_id": "cpu-regression",
                        "runtime_profile": {
                            "memory_mb": 1536,
                            "cpu_count": 2,
                            "shm_mb": 128,
                            "prediction_timeout_seconds": 19,
                            "threads": 2,
                            "warmup_runs": 2,
                            "defaults": {
                                "confidence": confidence,
                                **(
                                    {"image_size": 96}
                                    if confidence == 0.6
                                    and record.progress.get("dynamic_fixture")
                                    else {}
                                ),
                            },
                            "allowed_overrides": [],
                        },
                    },
                )
                assert response.status_code == 201, response.text
                deployment = response.json()
                deployments.append(deployment["id"])
                container.deployment_orchestrator.deploy(UUID(deployment["id"]))
                deployment = client.get(
                    f"/api/v1/deployments/{deployment['id']}"
                ).json()
                assert deployment["status"] == "running", deployment
                readiness = deployment["readiness"]
                assert readiness["threads"] == 2 and readiness["warmup_runs"] == 2
                with httpx.Client(
                    transport=httpx.HTTPTransport(uds="/var/run/docker.sock")
                ) as docker_client:
                    raw = docker_client.get(
                        f"http://docker/containers/{deployment['instance_id']}/json"
                    ).json()
                host_config = raw["HostConfig"]
                assert host_config["Memory"] == 1536 * 1024**2
                assert host_config["NanoCpus"] == 2_000_000_000
                assert host_config["ShmSize"] == 128 * 1024**2
                evidence["docker"].append(
                    {key: host_config[key] for key in ("Memory", "NanoCpus", "ShmSize")}
                )
                evidence["deployments"].append(deployment)
            assert len({d["instance_id"] for d in evidence["deployments"]}) == 2
            asset = client.post(
                "/api/v1/assets/upload",
                files=[("files", ("sample.png", image.getvalue(), "image/png"))],
            ).json()["assets"][0]
            for deployment in evidence["deployments"]:
                payload = {
                    "algorithm_version_id": model["id"],
                    "deployment_id": deployment["id"],
                    "asset_id": asset["id"],
                }
                denied = client.post(
                    "/api/v1/tasks",
                    json={**payload, "parameters": {"confidence": 0.99}},
                )
                assert denied.status_code == 400, denied.text
                response = client.post("/api/v1/tasks", json=payload)
                assert response.status_code == 201, response.text
                task_id = response.json()["id"]
                for _ in range(100):
                    task = client.get(f"/api/v1/tasks/{task_id}").json()
                    if task["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.2)
                assert task["status"] == "completed", task
                assert task["container_id"] == deployment["instance_id"]
                assert task["deployment_id"] == deployment["id"]
                task["result"] = client.get(f"/api/v1/tasks/{task_id}/result").json()[
                    "result"
                ]
                assert (
                    task["result"]["configuration"]["effective"]["conf"]
                    == deployment["runtime_profile"]["defaults"]["confidence"]
                )
                expected_size = deployment["runtime_profile"]["defaults"].get(
                    "image_size", model["parameters"]["image_size"]["default"]
                )
                if isinstance(expected_size, int):
                    expected_size = [expected_size, expected_size]
                configuration = task["result"]["configuration"]
                details = json.dumps({
                    "expected_size": expected_size,
                    "configuration": configuration,
                    "readiness": deployment["readiness"],
                    "profile": deployment["runtime_profile"],
                }, ensure_ascii=False)
                assert configuration["effective"]["imgsz"] == expected_size, details
                assert configuration["input_tensor_shape"] == [1, 3, *expected_size], details
                assert deployment["readiness"]["warmup_input_shape"] == [1, 3, *expected_size], details
                evidence["predictions"].append(task)
            # Service route: bind to first running deployment, switch to second, drain, and rollback.
            first, second = evidence["deployments"]
            route = client.put(
                "/api/v1/deployments/routes/runtime-service",
                json={"name": "ONNX 回归服务", "target_deployment_id": first["id"], "drain_timeout_seconds": 30},
            )
            assert route.status_code == 200, route.text
            routed_task = client.post(
                "/api/v1/tasks",
                json={"service_key": "runtime-service", "asset_id": asset["id"]},
            )
            assert routed_task.status_code == 201 and routed_task.json()["deployment_id"] == first["id"]
            routed_id = routed_task.json()["id"]
            for _ in range(100):
                if client.get(f"/api/v1/tasks/{routed_id}").json()["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.2)
            assert client.get(f"/api/v1/tasks/{routed_id}").json()["status"] == "completed"
            switched = client.put(
                "/api/v1/deployments/routes/runtime-service",
                json={"name": "ONNX 回归服务", "target_deployment_id": second["id"], "drain_timeout_seconds": 30},
            )
            assert switched.status_code == 200 and switched.json()["active_deployment_id"] == second["id"]
            assert client.get(f"/api/v1/deployments/{first['id']}").json()["status"] == "stopped"
            assert client.delete("/api/v1/deployments/routes/runtime-service").status_code == 204
            evidence["service_route"] = switched.json()
            # Restart reuses only this deployment; redeploy creates a fresh container.
            first = evidence["deployments"][0]
            assert (
                client.post(f"/api/v1/deployments/{first['id']}/start").status_code
                == 200
            )
            container.deployment_orchestrator.deploy(UUID(first["id"]))
            restarted = client.get(f"/api/v1/deployments/{first['id']}").json()
            assert (
                restarted["status"] == "running"
                and restarted["instance_id"] == first["instance_id"]
            )
            assert (
                client.post(f"/api/v1/deployments/{first['id']}/redeploy").status_code
                == 200
            )
            container.deployment_orchestrator.deploy(UUID(first["id"]))
            replaced = client.get(f"/api/v1/deployments/{first['id']}").json()
            assert (
                replaced["status"] == "running"
                and replaced["instance_id"] != first["instance_id"]
            )
            evidence["restart"] = restarted
            evidence["redeploy"] = replaced
        finally:
            for deployment_id in deployments:
                response = client.delete(f"/api/v1/deployments/{deployment_id}")
                assert response.status_code == 204, response.text
        assert not manager.node_client("cpu-regression").request("GET", "/instances")
    output = Path("/evidence/RUNTIME-VERIFIED.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(
        "ONNX publish, two isolated RuntimeProfiles, Docker resources, ORT warmup, exact-instance "
        "remote prediction, locked parameters, stop/start/redeploy/delete PASSED",
        flush=True,
    )


if __name__ == "__main__":
    verify()
