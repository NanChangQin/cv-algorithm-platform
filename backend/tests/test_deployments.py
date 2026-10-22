from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from cv_algorithm_sdk import AlgorithmStatus
from fastapi.testclient import TestClient

from cv_platform.core.errors import ApplicationError
from cv_platform.dependencies import get_container
from cv_platform.modules.instances.domain.models import RuntimeInstance

IMAGE = "cv-platform/faster-rcnn:dev"


class Node:
    """模拟一台服务器上的 Algorithm Manager：镜像、权重目录和实例。"""

    def __init__(self, node_id: str, name: str, *, images=(), models=None, gpus=None) -> None:
        self.node_id, self.node_name = node_id, name
        self.images = set(images)
        self.models: dict[str, dict] = models or {}
        self.gpu_list = gpus if gpus is not None else [
            {"index": 0, "memory_total_mb": 16000, "memory_used_mb": 12000},
            {"index": 1, "memory_total_mb": 16000, "memory_used_mb": 1000},
        ]
        self.instances: list[dict] = []
        self.calls: list[str] = []

    def gpus(self):
        return self.gpu_list

    def image_exists(self, image):
        return image in self.images

    def upload(self, path, source: Path, **_):
        self.calls.append(f"upload {path}")
        assert source.stat().st_size > 0
        return {"package_path": f"/packages/remote/{path.rsplit('/', 1)[-1]}"}

    def relay(self, target, source_path, target_path, *, params=None, headers=None, size=None,
              progress=None):
        if progress:
            progress(10, size)
        if source_path == "/images/export":
            target.images.add(params["image"])
        else:
            directory, name = source_path.removeprefix("/models/").split("/files/")
            item = self.models[directory][name]
            assert headers["X-Content-SHA256"] == item["sha256"]
            target.models.setdefault(directory, {})[name] = item
        target.calls.append(f"received {target_path}")

    def request(self, method, path, *, json=None, params=None, timeout=None, allow_missing=False):
        self.calls.append(f"{method} {path}")
        if path == "/images/inspect":
            return {"exists": params["image"] in self.images, "size": 6 * 1024**3}
        if method == "GET" and path.startswith("/models/"):
            return {"files": self.models.get(path.removeprefix("/models/"), {})}
        if path == "/images/build":
            self.images.add(json["image"])
            return {"image": json["image"]}
        if path == "/instances":
            return self.instances
        if path.endswith("/touch"):
            return {}
        raise AssertionError(f"unexpected {method} {path}")


WEIGHTS = {"faster-rcnn-resnet50": {"model.pth": {"size": 160_000_000, "sha256": "a" * 64}}}


@pytest.fixture
def cluster(monkeypatch):
    nodes = {
        "local": Node("local", "主服务器", images={IMAGE}, models=dict(WEIGHTS)),
        "dell": Node("dell", "Dell 5070 Ti"),
    }
    container = get_container()
    manager = container.algorithm_manager
    monkeypatch.setattr(manager, "node_client", lambda node_id: nodes[node_id])
    monkeypatch.setattr(manager, "node_clients", lambda: list(nodes.values()))
    monkeypatch.setattr(
        manager, "validate_node",
        lambda node_id: nodes.get(node_id) or (_ for _ in ()).throw(
            ApplicationError("RUNTIME_NODE_NOT_FOUND", "所选运行服务器不存在", 404)
        ),
    )
    started: list[dict] = []

    def start_algorithm(algorithm_id, project_id=None, *, node_id=None, gpu_device_ids=()):
        started.append({"node_id": node_id, "gpu_device_ids": gpu_device_ids})
        now = datetime.now(UTC)
        instance = RuntimeInstance(
            id=f"container-{len(started)}", algorithm_version_id=str(algorithm_id), image=IMAGE,
            container_name=f"cv-algorithm-test-{len(started)}", endpoint="http://x",
            status="healthy", device="gpu", created_at=now, updated_at=now, node_id=node_id,
            gpu_device_ids=gpu_device_ids,
        )
        nodes[node_id].instances.append({"id": instance.id, "status": "healthy", "error": None})
        return instance

    orchestrator = container.deployment_orchestrator
    monkeypatch.setattr(orchestrator._instances, "start_algorithm", start_algorithm)
    stopped: list[str] = []
    removed: list[str] = []
    monkeypatch.setattr(manager, "stop", lambda instance_id: stopped.append(instance_id))
    monkeypatch.setattr(manager, "remove", lambda instance_id: removed.append(instance_id))
    return type("Cluster", (), {
        "nodes": nodes, "started": started, "stopped": stopped, "removed": removed,
        "orchestrator": orchestrator,
    })


def detector_id(client: TestClient) -> str:
    return next(item["id"] for item in client.get("/api/v1/algorithms").json()
                if item["key"] == "faster-rcnn-resnet50")


def deploy(client: TestClient, algorithm_id: str, node_id="dell", gpus=None) -> dict:
    payload = {"algorithm_version_id": algorithm_id, "node_id": node_id}
    if gpus is not None:
        payload["gpu_device_ids"] = gpus
    response = client.post("/api/v1/deployments", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def current(client: TestClient, deployment_id: str) -> dict:
    return client.get(f"/api/v1/deployments/{deployment_id}").json()


def run(cluster, deployment: dict) -> None:
    cluster.orchestrator.deploy(UUID(deployment["id"]))


def test_missing_image_and_weights_are_copied_from_another_server(client, cluster) -> None:
    deployment = deploy(client, detector_id(client))
    # 未指定 GPU：选空闲显存最多的一张
    assert deployment["gpu_device_ids"] == ["1"]
    assert [step["status"] for step in deployment["steps"]] == ["pending"] * 4
    run(cluster, deployment)
    result = current(client, deployment["id"])
    assert result["status"] == "running"
    assert result["instance_id"] == "container-1"
    steps = {step["key"]: step for step in result["steps"]}
    assert all(step["status"] == "done" for step in steps.values())
    assert "从「主服务器」传输镜像" in steps["image"]["detail"]
    assert "复制 1 个权重文件" in steps["weights"]["detail"]
    assert "空闲显存 15000 MB" in steps["node"]["detail"]
    dell = cluster.nodes["dell"]
    assert IMAGE in dell.images
    assert "received /models/faster-rcnn-resnet50/files/model.pth" in dell.calls
    assert cluster.started == [{"node_id": "dell", "gpu_device_ids": ("1",)}]


def test_ready_servers_skip_transfers(client, cluster) -> None:
    deployment = deploy(client, detector_id(client), node_id="local", gpus=["0"])
    run(cluster, deployment)
    steps = {step["key"]: step for step in current(client, deployment["id"])["steps"]}
    assert steps["image"]["detail"] == f"服务器上已有镜像 {IMAGE}"
    assert steps["weights"]["detail"].startswith("服务器上已有 1 个权重文件")
    # 显存不够时提醒，但不阻止部署
    assert "可能启动失败" in steps["node"]["detail"]
    assert not any(call.startswith("received") for call in cluster.nodes["local"].calls)


def test_partial_model_directory_does_not_skip_missing_weights(client, cluster) -> None:
    source = cluster.nodes["local"]
    target = cluster.nodes["dell"]
    manifest = {"size": 200, "sha256": "b" * 64}
    source.models["faster-rcnn-resnet50"]["manifest.yaml"] = manifest
    target.models["faster-rcnn-resnet50"] = {"manifest.yaml": manifest}
    deployment = deploy(client, detector_id(client))
    run(cluster, deployment)
    assert current(client, deployment["id"])["status"] == "running"
    assert "received /models/faster-rcnn-resnet50/files/model.pth" in target.calls
    assert "received /models/faster-rcnn-resnet50/files/manifest.yaml" not in target.calls


def test_weights_missing_everywhere_are_skipped(client, cluster) -> None:
    cluster.nodes["local"].models = {}
    deployment = deploy(client, detector_id(client))
    run(cluster, deployment)
    result = current(client, deployment["id"])
    assert result["status"] == "running"
    weights = next(step for step in result["steps"] if step["key"] == "weights")
    assert weights["status"] == "skipped"


def test_imported_package_is_built_on_the_target_when_no_server_has_the_image(
    client, cluster
) -> None:
    template = client.get("/api/v1/algorithms/template?task_type=object_detection")
    imported = client.post(
        "/api/v1/algorithms/import",
        files={"package": ("template.zip", template.content, "application/zip")},
    ).json()
    algorithms = get_container().algorithms
    version = algorithms.get(UUID(imported["id"]))
    algorithms.save(version.with_status(AlgorithmStatus.AVAILABLE))
    deployment = deploy(client, imported["id"])
    run(cluster, deployment)
    result = current(client, deployment["id"])
    assert result["status"] == "running", result
    dell = cluster.nodes["dell"]
    assert f"upload /packages/{version.id.hex}" in dell.calls
    assert "POST /images/build" in dell.calls
    assert version.image in dell.images


def test_failure_is_shown_on_the_step_that_failed(client, cluster) -> None:
    cluster.nodes["local"].images = set()
    deployment = deploy(client, detector_id(client))
    run(cluster, deployment)
    result = current(client, deployment["id"])
    assert result["status"] == "failed"
    assert "所有服务器上都没有镜像" in result["error"]
    statuses = [step["status"] for step in result["steps"]]
    assert statuses == ["done", "failed", "pending", "pending"]


def test_stop_start_redeploy_and_delete(client, cluster) -> None:
    deployment = deploy(client, detector_id(client))
    run(cluster, deployment)
    url = f"/api/v1/deployments/{deployment['id']}"
    assert client.post(f"{url}/start").status_code == 409

    stopped = client.post(f"{url}/stop").json()
    assert stopped["status"] == "stopped"
    assert cluster.stopped == ["container-1"]

    assert client.post(f"{url}/start").json()["status"] == "queued"
    run(cluster, deployment)
    assert current(client, deployment["id"])["status"] == "running"

    redeployed = client.post(f"{url}/redeploy").json()
    assert redeployed["status"] == "queued"
    assert redeployed["instance_id"] is None
    assert cluster.removed == ["container-2"]

    assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 404


def test_lost_instances_are_reported_and_live_ones_kept_warm(client, cluster) -> None:
    deployment = deploy(client, detector_id(client))
    run(cluster, deployment)
    dell = cluster.nodes["dell"]
    result = get_container().deployments.get(UUID(deployment["id"]))
    cluster.orchestrator.check([result])
    assert "POST /instances/container-1/touch" in dell.calls
    assert current(client, deployment["id"])["status"] == "running"

    dell.instances.clear()
    cluster.orchestrator.check([result])
    lost = current(client, deployment["id"])
    assert lost["status"] == "failed"
    assert lost["error"].startswith("实例已被删除")


def test_deployment_requests_are_validated(client, cluster) -> None:
    algorithm_id = detector_id(client)
    deploy(client, algorithm_id, gpus=["0"])
    duplicate = client.post("/api/v1/deployments", json={
        "algorithm_version_id": algorithm_id, "node_id": "dell", "gpu_device_ids": ["0"],
    })
    assert duplicate.status_code == 409
    wrong_gpu = client.post("/api/v1/deployments", json={
        "algorithm_version_id": algorithm_id, "node_id": "dell", "gpu_device_ids": ["5"],
    })
    assert wrong_gpu.json()["code"] == "GPU_UNAVAILABLE"
    cluster.nodes["dell"].gpu_list = []
    no_gpu = client.post("/api/v1/deployments", json={
        "algorithm_version_id": algorithm_id, "node_id": "dell",
    })
    assert no_gpu.status_code == 409
    client.post(f"/api/v1/algorithm-versions/{algorithm_id}/disable")
    disabled = client.post("/api/v1/deployments", json={
        "algorithm_version_id": algorithm_id, "node_id": "local",
    })
    assert disabled.json()["code"] == "ALGORITHM_NOT_AVAILABLE"


def test_inference_can_target_a_running_deployment(client, cluster) -> None:
    algorithm_id = detector_id(client)
    deployment = deploy(client, algorithm_id)
    asset = client.post(
        "/api/v1/assets/upload",
        files=[("files", ("a.png", _png(), "image/png"))],
    ).json()["assets"][0]
    payload = {"algorithm_version_id": algorithm_id, "asset_id": asset["id"],
               "deployment_id": deployment["id"]}
    not_running = client.post("/api/v1/tasks", json=payload)
    assert not_running.json()["code"] == "DEPLOYMENT_NOT_RUNNING"

    run(cluster, deployment)
    task = client.post("/api/v1/tasks", json=payload)
    assert task.status_code == 201, task.text
    assert task.json()["node_id"] == "dell"
    assert task.json()["gpu_device_ids"] == ["1"]


def test_deleting_a_version_removes_its_deployments(client, cluster) -> None:
    template = client.get("/api/v1/algorithms/template?task_type=object_detection")
    imported = client.post(
        "/api/v1/algorithms/import",
        files={"package": ("template.zip", template.content, "application/zip")},
    ).json()
    algorithms = get_container().algorithms
    algorithms.save(algorithms.get(UUID(imported["id"])).with_status(AlgorithmStatus.AVAILABLE))
    deployment = deploy(client, imported["id"])
    response = client.delete(f"/api/v1/algorithm-versions/{imported['id']}")
    assert response.status_code == 204
    assert client.get(f"/api/v1/deployments/{deployment['id']}").status_code == 404


def _png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_users_can_see_deployments_but_only_developers_change_them(client, cluster) -> None:
    deployment = deploy(client, detector_id(client))
    client.post(
        "/api/v1/users",
        json={"username": "plain-user", "password": "Plain-Pass-123!", "role": "user"},
    )
    project = client.get("/api/v1/projects").json()[0]
    client.post(f"/api/v1/projects/{project['id']}/members",
                json={"username": "plain-user", "role": "editor"})
    login = client.post(
        "/api/v1/auth/login", json={"username": "plain-user", "password": "Plain-Pass-123!"}
    )
    assert login.status_code == 200
    assert [item["id"] for item in client.get("/api/v1/deployments").json()] == [deployment["id"]]
    assert client.post(f"/api/v1/deployments/{deployment['id']}/stop").status_code == 403
    assert client.post("/api/v1/deployments", json={
        "algorithm_version_id": detector_id(client), "node_id": "local",
    }).status_code == 403
    assert client.get("/api/v1/training/runs").status_code == 403


def test_published_models_upload_their_weights_from_the_main_server(client, cluster) -> None:
    from dataclasses import replace

    from cv_platform.modules.algorithms.domain.entities import AlgorithmVersion
    from cv_platform.modules.training.publish import inference_manifest

    container = get_container()
    manifest = inference_manifest("helmet-detector", "安全帽", "1.0.0", "", 640)
    version = AlgorithmVersion.available(manifest, "cv-platform/yolo:dev")
    store = container.model_store.directory(version.id)
    store.mkdir(parents=True)
    weights = store / "model.pt"
    weights.write_bytes(b"w" * 2048)
    files = {"model.pt": {"size": 2048, "sha256": "c" * 64}}
    version = replace(version, model_directory=f"trained/{version.id.hex}",
                      lineage={"files": files})
    container.algorithms.add(version)
    cluster.nodes["dell"].images.add("cv-platform/yolo:dev")

    deployment = deploy(client, str(version.id))
    run(cluster, deployment)
    result = current(client, deployment["id"])
    weights_step = next(step for step in result["steps"] if step["key"] == "weights")
    assert result["status"] == "running", result
    assert weights_step["detail"] == f"已上传 1 个文件到 models/trained/{version.id.hex}"
    assert f"upload /models/trained/{version.id.hex}/files/model.pt" in cluster.nodes["dell"].calls
