from uuid import UUID

from cv_algorithm_sdk import TaskStatus
from test_deployments import _png, current, deploy, detector_id, run
from test_deployments import cluster as _cluster_fixture

from cv_platform.dependencies import get_container
from cv_platform.modules.deployments.domain import DeploymentStatus

cluster = _cluster_fixture


def test_route_waits_for_candidate_readiness_drains_old_deployment_and_rolls_back(
    client, cluster
) -> None:
    algorithm_id = detector_id(client)
    d1 = deploy(client, algorithm_id, node_id="local", gpus=["0"])
    run(cluster, d1)
    assert current(client, d1["id"])["status"] == "running"

    # 1. 首次创建服务路由必须指向运行中的部署实例
    d2 = deploy(client, algorithm_id, node_id="dell", gpus=["1"])
    not_ready = client.put(
        "/api/v1/deployments/routes/helmet-prod",
        json={
            "name": "安全帽线上服务",
            "target_deployment_id": d2["id"],
            "drain_timeout_seconds": 30,
        },
    )
    assert not_ready.status_code == 409
    assert not_ready.json()["code"] == "DEPLOYMENT_NOT_RUNNING"

    created = client.put(
        "/api/v1/deployments/routes/helmet-prod",
        json={
            "name": "安全帽线上服务",
            "target_deployment_id": d1["id"],
            "drain_timeout_seconds": 30,
        },
    )
    assert created.status_code == 200, created.text
    route = created.json()
    assert route["active_deployment_id"] == d1["id"]
    assert route["rollout_status"] == "active"

    # 2. 激活中的部署不允许直接删除
    in_use = client.delete(f"/api/v1/deployments/{d1['id']}")
    assert in_use.status_code == 409
    assert in_use.json()["code"] == "DEPLOYMENT_IN_USE"

    # 3. 通过 service_key 发起推理任务，自动绑定当前 active_deployment_id
    asset = client.post(
        "/api/v1/assets/upload",
        files=[("files", ("a.png", _png(), "image/png"))],
    ).json()["assets"][0]
    task1 = client.post(
        "/api/v1/tasks",
        json={"service_key": "helmet-prod", "asset_id": asset["id"]},
    )
    assert task1.status_code == 201, task1.text
    assert task1.json()["deployment_id"] == d1["id"]
    assert task1.json()["node_id"] == "local"

    # 4. 切换到尚未就绪的 d2：保持 d1 线上服务，d2 进入 candidate
    staged = client.put(
        "/api/v1/deployments/routes/helmet-prod",
        json={"target_deployment_id": d2["id"], "drain_timeout_seconds": 30},
    )
    assert staged.status_code == 200
    assert staged.json()["active_deployment_id"] == d1["id"]
    assert staged.json()["candidate_deployment_id"] == d2["id"]
    assert staged.json()["rollout_status"] == "switching"

    # 切换期间新请求仍路由到 d1
    task_during_switch = client.post(
        "/api/v1/tasks",
        json={"service_key": "helmet-prod", "asset_id": asset["id"]},
    )
    assert task_during_switch.status_code == 201
    assert task_during_switch.json()["deployment_id"] == d1["id"]

    # 5. d2 部署就绪并验证通过后，自动切流到 d2；d1 因存在在途任务进入 draining
    run(cluster, d2)
    promoted = client.get("/api/v1/deployments/routes/helmet-prod").json()
    assert promoted["active_deployment_id"] == d2["id"]
    assert promoted["previous_deployment_id"] == d1["id"]
    assert promoted["candidate_deployment_id"] is None
    assert promoted["rollout_status"] == "draining"
    d1_state = current(client, d1["id"])
    assert d1_state["status"] == "draining"
    assert d1_state["in_flight_tasks"] == 2
    assert cluster.stopped == []

    # draining 实例拒绝新直接绑定请求，而 service_key 新请求已切到 d2
    direct_to_draining = client.post(
        "/api/v1/tasks",
        json={
            "algorithm_version_id": algorithm_id,
            "asset_id": asset["id"],
            "deployment_id": d1["id"],
        },
    )
    assert direct_to_draining.status_code == 409
    assert direct_to_draining.json()["code"] == "DEPLOYMENT_DRAINING"

    task_on_d2 = client.post(
        "/api/v1/tasks",
        json={"service_key": "helmet-prod", "asset_id": asset["id"]},
    )
    assert task_on_d2.status_code == 201
    assert task_on_d2.json()["deployment_id"] == d2["id"]
    assert task_on_d2.json()["node_id"] == "dell"

    # 6. 已在 d1 排队的在途任务完成排空后，d1 自动停止容器并进入 stopped
    tasks = get_container().tasks
    for tid in (task1.json()["id"], task_during_switch.json()["id"]):
        item = tasks.get(UUID(tid))
        tasks.save(item.transition(TaskStatus.CANCELLED, cancelled_by="test"))
    cluster.orchestrator.reconcile_draining()
    assert current(client, d1["id"])["status"] == "stopped"
    assert cluster.stopped == ["container-1"]

    # 7. 手动回滚：d1 已停止，触发回滚会自动将 d1 重新排队启动并在就绪后切回
    rolled_back = client.post("/api/v1/deployments/routes/helmet-prod/rollback")
    assert rolled_back.status_code == 200
    assert rolled_back.json()["candidate_deployment_id"] == d1["id"]
    assert current(client, d1["id"])["status"] == "queued"

    # 完成 task_on_d2，这样 d2 在切流后无在途任务可直接停止
    t_d2 = tasks.get(UUID(task_on_d2.json()["id"]))
    tasks.save(t_d2.transition(TaskStatus.CANCELLED, cancelled_by="test"))
    run(cluster, d1)
    after_rollback = client.get("/api/v1/deployments/routes/helmet-prod").json()
    assert after_rollback["active_deployment_id"] == d1["id"]
    assert after_rollback["previous_deployment_id"] == d2["id"]
    assert after_rollback["candidate_deployment_id"] is None
    assert current(client, d1["id"])["status"] == "running"
    assert current(client, d2["id"])["status"] == "stopped"


def test_failed_candidate_keeps_active_deployment_and_records_reason(client, cluster) -> None:
    algorithm_id = detector_id(client)
    d1 = deploy(client, algorithm_id, node_id="local", gpus=["0"])
    run(cluster, d1)
    client.put(
        "/api/v1/deployments/routes/helmet-prod",
        json={"target_deployment_id": d1["id"]},
    )

    # d2 目标节点 GPU 被移除导致部署失败
    d2 = deploy(client, algorithm_id, node_id="dell", gpus=["1"])
    client.put(
        "/api/v1/deployments/routes/helmet-prod",
        json={"target_deployment_id": d2["id"]},
    )
    cluster.nodes["dell"].gpu_list = []
    run(cluster, d2)

    assert current(client, d2["id"])["status"] == "failed"
    route = client.get("/api/v1/deployments/routes/helmet-prod").json()
    assert route["active_deployment_id"] == d1["id"]
    assert route["candidate_deployment_id"] is None
    assert route["rollout_status"] == "active"
    assert "候选部署未就绪" in (route["last_error"] or "")


def test_active_deployment_failure_triggers_automatic_rollback_and_drain_timeout(
    client, cluster
) -> None:
    algorithm_id = detector_id(client)
    d1 = deploy(client, algorithm_id, node_id="local", gpus=["0"])
    run(cluster, d1)
    client.put(
        "/api/v1/deployments/routes/helmet-prod",
        json={"target_deployment_id": d1["id"], "drain_timeout_seconds": 10},
    )
    asset = client.post(
        "/api/v1/assets/upload",
        files=[("files", ("a.png", _png(), "image/png"))],
    ).json()["assets"][0]
    stuck_task = client.post(
        "/api/v1/tasks",
        json={"service_key": "helmet-prod", "asset_id": asset["id"]},
    ).json()

    d2 = deploy(client, algorithm_id, node_id="dell", gpus=["1"])
    run(cluster, d2)
    client.put(
        "/api/v1/deployments/routes/helmet-prod",
        json={"target_deployment_id": d2["id"], "drain_timeout_seconds": 10},
    )
    # d1 仍在排空中，若此时 d2 容器崩溃，健康检查应将流量立即切回仍在运行的 d1
    cluster.nodes["dell"].instances.clear()
    cluster.orchestrator.check([get_container().deployments.get(UUID(d2["id"]))])

    route = client.get("/api/v1/deployments/routes/helmet-prod").json()
    assert route["active_deployment_id"] == d1["id"]
    assert route["previous_deployment_id"] == d2["id"]
    assert current(client, d1["id"])["status"] == "running"
    assert "自动回滚" in (route["last_error"] or "")

    # 验证排空超时强制停止：手动停止 d1（因有 stuck_task 进入 draining），超时后自动停止
    stopped = client.post(f"/api/v1/deployments/{d1['id']}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "draining"
    repo = get_container().deployments
    repo.update(UUID(d1["id"]), {DeploymentStatus.DRAINING}, status=DeploymentStatus.DRAINING)
    # 模拟超时：再次调用 stop 立即停止，或等待超时
    force_stopped = client.post(f"/api/v1/deployments/{d1['id']}/stop")
    assert force_stopped.status_code == 200
    assert force_stopped.json()["status"] == "stopped"
    assert stuck_task["id"]


def test_operations_metrics_percentiles_and_dataset_feedback_create_new_version(
    client, cluster
) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from cv_algorithm_sdk.results import (
        AlgorithmIdentity,
        BoundingBox,
        Detection,
        ImageMetadata,
        ObjectDetectionData,
        ObjectDetectionResult,
        TimingMetadata,
    )
    from test_training import make_zip

    algorithm_id = detector_id(client)
    d1 = deploy(client, algorithm_id, node_id="local", gpus=["0"])
    run(cluster, d1)
    client.put(
        "/api/v1/deployments/routes/helmet-prod",
        json={"target_deployment_id": d1["id"]},
    )

    asset = client.post(
        "/api/v1/assets/upload",
        files=[("files", ("sample.png", _png(), "image/png"))],
    ).json()["assets"][0]

    container = get_container()
    now = datetime.now(UTC)
    created_task_ids: list[str] = []
    for idx, inf_ms in enumerate([10.0, 20.0, 40.0, 100.0]):
        resp = client.post(
            "/api/v1/tasks",
            json={"service_key": "helmet-prod", "asset_id": asset["id"]},
        )
        assert resp.status_code == 201
        tid = UUID(resp.json()["id"])
        created_task_ids.append(str(tid))
        task = container.tasks.get(tid)
        assert task is not None
        c_at = now - timedelta(seconds=10)
        s_at = c_at + timedelta(milliseconds=5 * (idx + 1))
        f_at = s_at + timedelta(milliseconds=inf_ms)
        res = ObjectDetectionResult(
            request_id=str(tid),
            algorithm=AlgorithmIdentity(id="helmet-detector", version="1.0.0"),
            input=ImageMetadata(width=16, height=16),
            timing=TimingMetadata(
                preprocess_ms=1.0,
                inference_ms=inf_ms,
                postprocess_ms=1.0,
            ),
            data=ObjectDetectionData(
                detections=[
                    Detection(
                        label="person",
                        score=0.9 if idx % 2 == 0 else 0.3,
                        bbox=BoundingBox([2.0, 2.0, 12.0, 14.0]),
                    )
                ]
            ),
        )
        container.tasks.save(
            replace(
                task,
                status=TaskStatus.COMPLETED,
                created_at=c_at,
                started_at=s_at,
                completed_at=f_at,
                result=res,
            )
        )

    # 模拟 1 个失败任务验证错误率统计
    fail_resp = client.post(
        "/api/v1/tasks",
        json={"service_key": "helmet-prod", "asset_id": asset["id"]},
    )
    fail_id = UUID(fail_resp.json()["id"])
    fail_task = container.tasks.get(fail_id)
    assert fail_task is not None
    container.tasks.save(
        replace(
            fail_task,
            status=TaskStatus.FAILED,
            created_at=now - timedelta(seconds=5),
            started_at=now - timedelta(seconds=4),
            completed_at=now - timedelta(seconds=3),
        )
    )

    metrics = client.get(
        f"/api/v1/operations/metrics?deployment_id={d1['id']}&window_seconds=60"
    )
    assert metrics.status_code == 200, metrics.text
    mdata = metrics.json()
    assert mdata["completed_samples"] == 4
    assert mdata["failed_tasks"] == 1
    assert mdata["error_rate"] == 0.2
    assert mdata["qps_1m"] > 0
    assert mdata["inference_p50_ms"] == 30.0
    assert mdata["inference_p95_ms"] > 80.0
    assert mdata["inference_p99_ms"] > 95.0
    assert mdata["queue_wait_p50_ms"] > 0
    assert d1["id"] in mdata["deployment_metrics"]
    assert mdata["deployment_metrics"][d1["id"]]["completed_samples"] == 4

    # 创建基础训练数据集版本 v1（类别包含 person, helmet）
    entries = {
        "data.yaml": "names: [person, helmet]\n",
        "images/train/base1.png": _png(),
        "labels/train/base1.txt": "0 0.5 0.5 0.4 0.4\n",
        "images/val/base2.png": _png(),
        "labels/val/base2.txt": "1 0.5 0.5 0.4 0.4\n",
    }
    ds_resp = client.post(
        "/api/v1/training/datasets",
        data={"name": "helmet-feedback-ds"},
        files={"file": ("dataset.zip", make_zip(entries), "application/zip")},
    )
    assert ds_resp.status_code == 201, ds_resp.text
    ds_v1 = ds_resp.json()
    assert ds_v1["version"] == 1
    assert ds_v1["stats"]["splits"]["train"]["images"] == 1

    # 将推理任务样本回流到数据集（含自动从检测结果提取高置信度框 + 显式修正框）
    fb_resp = client.post(
        f"/api/v1/training/datasets/{ds_v1['id']}/feedback",
        json={
            "samples": [
                {"task_id": created_task_ids[0]},
                {
                    "task_id": created_task_ids[1],
                    "boxes": [{"label": "helmet", "bbox": [1.0, 1.0, 10.0, 10.0]}],
                },
            ],
            "target_split": "train",
            "min_confidence": 0.5,
        },
    )
    assert fb_resp.status_code == 201, fb_resp.text
    ds_v2 = fb_resp.json()
    assert ds_v2["family_id"] == ds_v1["family_id"]
    assert ds_v2["parent_id"] == ds_v1["id"]
    assert ds_v2["version"] == 2
    assert ds_v2["stats"]["splits"]["train"]["images"] == 3
    assert ds_v2["stats"]["splits"]["train"]["boxes"] == 3