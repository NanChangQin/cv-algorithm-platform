import contextlib
import hashlib
import io
import json
import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, RLock
from urllib.parse import unquote, urlparse
from uuid import UUID, uuid4

import httpx
from cv_algorithm_sdk import AlgorithmStatus
from PIL import Image, ImageDraw, ImageFont

from ...core.errors import ApplicationError
from ..assets.application.service import AssetService
from ..tasks.application.service import TaskService
from .store import SessionStore
from .tracking import IoUTracker

TERMINAL = {"completed", "failed", "stopped", "interrupted"}
log = logging.getLogger(__name__)


def now():
    return datetime.now(UTC).isoformat()


class InferenceService:
    def __init__(self, database, root, worker_url):
        self.store = SessionStore(database)
        self.root = Path(root) / "inference"
        self.root.mkdir(parents=True, exist_ok=True)
        self.worker_url = worker_url.rstrip("/")
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="inference-session")
        self.controls = {}
        self.lock = RLock()

    def update(self, session_id, **values):
        with self.lock:
            state = self.store.get(session_id)
            state.update(values, updated_at=now())
            return self.store.save(state)

    def create(self, config, project_id, actor, container):
        config = dict(config)
        algorithm = container.algorithms.get(UUID(config["algorithm_version_id"]))
        if not algorithm or algorithm.project_id != project_id:
            raise ApplicationError("ALGORITHM_NOT_FOUND", "算法版本不存在", 404)
        if algorithm.status != AlgorithmStatus.AVAILABLE:
            raise ApplicationError("ALGORITHM_UNAVAILABLE", "模型当前不可用", 409)
        if config["tracking"] and (
            config["kind"] == "images" or algorithm.manifest.task_type.value != "object_detection"
        ):
            raise ApplicationError(
                "TRACKING_UNSUPPORTED", "目标跟踪仅适用于视频/实时流的目标检测模型"
            )
        try:
            config["parameters"] = algorithm.manifest.resolve_parameters(config["parameters"])
        except ValueError as exc:
            raise ApplicationError("PARAMETER_INVALID", str(exc)) from exc
        if config.get("service_key"):
            route = (
                container.deployment_routes.get(config["service_key"], project_id)
                if hasattr(container, "deployment_routes")
                else None
            )
            if not route:
                raise ApplicationError("SERVICE_INVALID", "请从模型部署入口选择可用部署实例")
        if config.get("deployment_id"):
            from ..deployments.policy import bound_deployment, resolve_parameters

            deployment = bound_deployment(
                container.deployments, UUID(config["deployment_id"]), project_id, algorithm.id
            )
            config["parameters"] = resolve_parameters(deployment, algorithm, config["parameters"])
            config["node_id"] = deployment.node_id
            config["gpu_device_ids"] = list(deployment.gpu_device_ids)
        if config["gpu_device_ids"] and not config.get("node_id"):
            raise ApplicationError("PLACEMENT_INVALID", "指定 GPU 时必须选择服务器")
        if config["gpu_device_ids"] and algorithm.manifest.runtime.device.value == "cpu":
            raise ApplicationError("PLACEMENT_INVALID", "CPU 模型不能指定 GPU")
        inputs = []
        source = None
        if config["kind"] == "images":
            if not config["asset_ids"]:
                raise ApplicationError("INPUT_REQUIRED", "请选择图片")
            assets = AssetService(container.assets, container.asset_storage, project_id, actor)
            for key in dict.fromkeys(config["asset_ids"]):
                asset = assets.get_asset(UUID(key))
                inputs.append(
                    {"id": str(asset.id), "name": asset.original_name, "sha256": asset.sha256}
                )
            config["asset_ids"] = [a["id"] for a in inputs]
        elif config["kind"] != "browser":
            if not config.get("source_id"):
                raise ApplicationError("SOURCE_REQUIRED", "请选择媒体源")
            source = container.media_sources.get(UUID(config["source_id"]), project_id)
            if not source.enabled:
                raise ApplicationError("SOURCE_DISABLED", "媒体源已禁用", 409)
            if source.source_type.value != config["kind"]:
                raise ApplicationError("SOURCE_MISMATCH", "媒体源类型与输入模式不一致")
        source_hash = None
        if source and config["kind"] == "video":
            relative = unquote(urlparse(source.uri).path).removeprefix("/data/")
            try:
                with container.asset_storage.resolve(relative).open("rb") as stream:
                    source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            except OSError:
                raise ApplicationError("VIDEO_MISSING", "视频文件不存在，请重新上传", 404) from None
        with self.lock:
            active = [s for s in self.store.list(limit=10000) if s["status"] not in TERMINAL]
            if len(active) >= 8:
                raise ApplicationError(
                    "INFERENCE_CAPACITY", "推理队列已满（最多 2 个运行、6 个排队会话）", 429
                )
            if config["kind"] == "browser" and len(active) >= 2:
                raise ApplicationError(
                    "INFERENCE_BUSY", "实时采集暂时没有空闲执行槽，请稍后再试", 409
                )
            session_id = str(uuid4())
            state = {
                "id": session_id,
                "project_id": str(project_id),
                "owner": actor,
                "name": config["name"].strip()
                or (source.name if source else algorithm.manifest.name),
                "kind": config["kind"],
                "status": "queued",
                "created_at": now(),
                "updated_at": now(),
                "started_at": None,
                "finished_at": None,
                "config": config,
                "model": {
                    "id": str(algorithm.id),
                    "name": algorithm.manifest.name,
                    "version": algorithm.manifest.version,
                    "task_type": algorithm.manifest.task_type.value,
                    "image_digest": algorithm.image_digest,
                    "package_sha256": algorithm.package_sha256,
                    "lineage": algorithm.lineage,
                },
                "source": {
                    "id": str(source.id),
                    "name": source.name,
                    "type": source.source_type.value,
                    "sha256": source_hash,
                }
                if source
                else None,
                "inputs": inputs,
                "processed": 0,
                "failed": 0,
                "tracks": 0,
                "segment": 0,
                "reconnections": 0,
                "skipped_frames": 0,
                "error": None,
                "notes": [],
                "artifacts": {},
                "bytes": 0,
                "expires_at": None,
                "total_frames": None,
                "source_fps": None,
                "input_bytes": 0,
                "result_bytes": 0,
            }
            self.controls[session_id] = {
                "stop": Event(),
                "queue": Queue(maxsize=1),
                "received": time.monotonic(),
            }
            self.store.save(state)
            self.executor.submit(self.execute, session_id, container)
            return state

    def stop(self, session_id, project_id):
        state = self.store.get(session_id, project_id)
        if state["status"] in TERMINAL:
            return state
        control = self.controls.get(str(session_id))
        if control:
            control["stop"].set()
        return self.update(session_id, status="stopping")

    def browser_frame(self, session_id, project_id, data):
        state = self.store.get(session_id, project_id)
        if state["kind"] != "browser" or state["status"] in TERMINAL | {"stopping"}:
            raise ApplicationError("CAPTURE_CLOSED", "采集会话已结束", 409)
        control = self.controls.get(str(session_id))
        if not control:
            raise ApplicationError("CAPTURE_INTERRUPTED", "采集会话已中断", 409)
        if len(data) > 8 * 1024 * 1024:
            raise ApplicationError("FRAME_TOO_LARGE", "单帧不能超过 8 MB", 413)
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 16000000:
                    raise ValueError("resolution")
                image.verify()
        except Exception:
            raise ApplicationError("FRAME_INVALID", "画面格式无效或分辨率过大") from None
        try:
            control["queue"].put_nowait((data, time.monotonic()))
        except Full:
            raise ApplicationError(
                "CAPTURE_BACKPRESSURE", "上一帧仍在处理中，请稍后发送", 429
            ) from None
        control["received"] = time.monotonic()
        return {"accepted": True}

    def worker(self, method, path, **kwargs):
        try:
            with httpx.Client(
                base_url=self.worker_url, timeout=kwargs.pop("timeout", 15), trust_env=False
            ) as client:
                r = client.request(method, path, **kwargs)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError:
            raise ApplicationError(
                "MEDIA_CONNECTION", "媒体源不可达、采集连接中断或媒体服务不可用", 502
            ) from None

    def execute(self, session_id, container):
        control = self.controls[session_id]
        stop = control["stop"]
        initial = self.store.get(session_id)
        config = initial["config"]
        kind = config["kind"]
        project = UUID(initial["project_id"])
        tasks = TaskService(
            container.tasks,
            container.algorithms,
            container.assets,
            container.task_queue,
            project,
            initial["owner"],
            deployments=container.deployments,
        )
        assets = AssetService(container.assets, container.asset_storage, project, initial["owner"])
        root = self.root / session_id
        root.mkdir(exist_ok=True)
        tracker = IoUTracker()
        start = time.monotonic()
        segment_start = start
        deadline = start + config["duration_seconds"] if config["duration_seconds"] else None
        source = None
        current_task = None
        opened = False
        frame_index = 0
        outcome = "completed"
        notes = []
        skipped_base = 0
        try:
            if config.get("source_id"):
                source = container.media_sources.get(UUID(config["source_id"]), project)
            if stop.is_set():
                return
            self.update(session_id, status="running", started_at=now())
            while frame_index < (
                len(config["asset_ids"]) if kind == "images" else config["max_frames"]
            ):
                if stop.is_set() or (deadline and time.monotonic() >= deadline):
                    outcome = "stopped"
                    notes.append("用户停止或达到定时停止时限")
                    break
                if shutil.disk_usage(root).free < 256 * 1024 * 1024:
                    raise ApplicationError("STORAGE_FULL", "存储空间不足，已停止继续采集")
                meta = {"source_index": frame_index, "timestamp_ms": None}
                if kind == "images":
                    asset = assets.get_asset(UUID(config["asset_ids"][frame_index]))
                elif kind == "browser":
                    try:
                        data, captured_at = control["queue"].get(timeout=1)
                    except Empty:
                        if time.monotonic() - control["received"] > 15:
                            outcome = "interrupted"
                            notes.append("浏览器超过 15 秒未提供画面，采集已中断")
                            break
                        continue
                    meta["timestamp_ms"] = (captured_at - start) * 1000
                    asset = assets.upload(
                        io.BytesIO(data), f"browser-{frame_index:08d}.jpg", "image/jpeg"
                    )
                else:
                    try:
                        if not opened:
                            self.worker(
                                "PUT",
                                f"/sessions/{session_id}",
                                json={
                                    "uri": source.uri,
                                    "interval_seconds": config["interval_seconds"],
                                    "live": kind != "video",
                                },
                            )
                            opened = True
                            segment_start = time.monotonic()
                            self.update(session_id, status="running")
                        meta = self.worker("GET", f"/sessions/{session_id}/next")
                        if meta.get("eof"):
                            break
                        if kind != "video":
                            meta["timestamp_ms"] += (segment_start - start) * 1000
                        self.update(
                            session_id,
                            source_fps=meta["fps"],
                            total_frames=meta["total_frames"] or None,
                            skipped_frames=skipped_base + meta.get("skipped_frames", 0),
                        )
                        asset = assets.register_local_image(
                            meta["relative_path"], f"frame-{frame_index:08d}.jpg"
                        )
                        container.asset_storage.resolve(meta["relative_path"]).unlink(
                            missing_ok=True
                        )
                    except ApplicationError:
                        if kind == "video":
                            raise
                        state = self.store.get(session_id)
                        if state["reconnections"] >= config["reconnect_attempts"]:
                            raise
                        self.update(
                            session_id,
                            status="reconnecting",
                            reconnections=state["reconnections"] + 1,
                            segment=state["segment"] + 1,
                        )
                        notes.append(
                            f"第 {state['reconnections'] + 1} 次断流重连，重新开始轨迹关联"
                        )
                        tracker = IoUTracker()
                        skipped_base = state["skipped_frames"]
                        opened = False
                        stop.wait(min(2 ** state["reconnections"], 8))
                        continue
                task = tasks.create_task(
                    UUID(config["algorithm_version_id"]),
                    config["parameters"],
                    asset_id=asset.id,
                    node_id=config.get("node_id"),
                    gpu_device_ids=tuple(config["gpu_device_ids"]),
                    deployment_id=UUID(config["deployment_id"])
                    if config.get("deployment_id")
                    else None,
                )
                current_task = task.id
                self.update(session_id, current_task_id=str(task.id))
                task_deadline = time.monotonic() + 180
                while task.status.value not in {"completed", "failed", "cancelled"}:
                    if stop.wait(0.25) or (deadline and time.monotonic() >= deadline):
                        with contextlib.suppress(ApplicationError):
                            tasks.cancel(task.id)
                        outcome = "stopped"
                        break
                    if time.monotonic() > task_deadline:
                        tasks.cancel(task.id)
                        raise ApplicationError("FRAME_TIMEOUT", "单帧处理超过 180 秒，任务已停止")
                    task = tasks.get_task(task.id)
                task = tasks.get_task(task.id)
                current_task = None
                state = self.store.get(session_id)
                result = task.result.model_dump(mode="json") if task.result else None
                tracks = []
                if result and config["tracking"]:
                    tracks = tracker.update(
                        result["data"]["detections"], (meta["timestamp_ms"] or 0) / 1000
                    )
                frame = {
                    "index": frame_index,
                    "source_index": meta["source_index"],
                    "timestamp_ms": meta["timestamp_ms"],
                    "captured_at": now(),
                    "segment": state["segment"],
                    "asset_id": str(asset.id),
                    "input_name": asset.original_name,
                    "input_sha256": asset.sha256,
                    "task_id": str(task.id),
                    "status": task.status.value,
                    "error": task.error_message,
                    "result": result,
                    "tracks": tracks,
                    "execution": {
                        "node": task.execution_node_id,
                        "gpus": list(task.execution_gpu_device_ids),
                    },
                    "started_at": task.started_at.isoformat() if task.started_at else None,
                    "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                }
                self.store.frame(session_id, frame)
                input_bytes = state.get("input_bytes", 0) + asset.size_bytes
                result_bytes = state.get("result_bytes", 0) + len(json.dumps(frame).encode())
                if result:
                    self.render(
                        root,
                        frame,
                        container.asset_storage.resolve(asset.storage_key),
                        config["save_video"],
                        config["save_recording"],
                    )
                size = sum(p.stat().st_size for p in root.iterdir() if p.is_file())
                tracks_total = state["tracks"] + sum(1 for t in tracks if len(t["trail"]) == 1)
                self.update(
                    session_id,
                    processed=frame_index + 1,
                    failed=state["failed"] + (0 if result else 1),
                    tracks=tracks_total,
                    bytes=size,
                    input_bytes=input_bytes,
                    result_bytes=result_bytes,
                    last_frame=frame_index,
                    current_task_id=None,
                )
                frame_index += 1
                if outcome == "stopped":
                    break
                if size + input_bytes + result_bytes >= config["storage_limit_mb"] * 1024 * 1024:
                    notes.append("已达到本次产物存储上限，停止继续采集")
                    outcome = "stopped"
                    break
                if kind not in {"images", "video"}:
                    stop.wait(max(0.05, config["interval_seconds"]))
            else:
                if kind != "images":
                    notes.append("已达到本次最大处理帧数")
            state = self.store.get(session_id)
            if state["failed"] and state["failed"] == state["processed"]:
                outcome = "failed"
                self.update(session_id, error="所有输入处理均失败，请检查帧任务详情")
            if state["failed"]:
                notes.append(f"{state['failed']} 帧处理失败，可查看明细")
            if frame_index == 0 and outcome == "completed":
                raise ApplicationError("MEDIA_EMPTY", "输入没有可处理的画面")
        except Exception as exc:
            outcome = "failed"
            message = (
                exc.message if isinstance(exc, ApplicationError) else "推理执行失败，请查看服务日志"
            )
            self.update(session_id, error=message)
            log.exception("inference session %s failed", session_id)
        finally:
            if stop.is_set():
                outcome = "stopped"
            if current_task:
                with contextlib.suppress(ApplicationError):
                    tasks.cancel(current_task)
            if opened:
                with contextlib.suppress(ApplicationError):
                    self.worker("DELETE", f"/sessions/{session_id}")
            artifacts = {}
            if kind != "images" and frame_index:
                self.update(session_id, status="finalizing")
                frames = [
                    {"index": f["index"], "timestamp_ms": f["timestamp_ms"]}
                    for f in self.store.frames(session_id, 10000)
                ]
                for raw, enabled in [
                    (False, config["save_video"]),
                    (True, config["save_recording"]),
                ]:
                    if enabled:
                        try:
                            self.worker(
                                "POST",
                                f"/sessions/{session_id}/video",
                                json={"frames": frames, "raw": raw},
                                timeout=330,
                            )
                            name = "recording.mp4" if raw else "result.mp4"
                            artifacts[name] = (root / name).stat().st_size
                        except (ApplicationError, OSError):
                            notes.append("部分视频产物生成失败；逐帧结果仍可查看")
            from datetime import timedelta

            expiry = (datetime.now(UTC) + timedelta(days=config["retention_days"])).isoformat()
            self.update(
                session_id,
                status=outcome,
                finished_at=now(),
                notes=notes,
                artifacts=artifacts,
                expires_at=expiry,
                current_task_id=None,
                bytes=sum(p.stat().st_size for p in root.iterdir() if p.is_file()),
            )
            with self.lock:
                self.controls.pop(session_id, None)

    @staticmethod
    def render(root, frame, path, save_video, save_recording):
        with Image.open(path) as source:
            image = source.convert("RGB")
        index = frame["index"]
        if save_recording:
            image.save(root / f"recording-{index:08d}.jpg", quality=85)
        draw = ImageDraw.Draw(image)
        font_path = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
        font = (
            ImageFont.truetype(font_path, max(12, image.width // 65))
            if Path(font_path).exists()
            else ImageFont.load_default()
        )
        result = frame["result"]
        if result["type"] == "object_detection":
            for d in frame["tracks"] or result["data"]["detections"]:
                draw.rectangle(d["bbox"], outline="#32d399", width=2)
                draw.text(
                    (d["bbox"][0], max(0, d["bbox"][1] - 18)),
                    f"{d['label']} {d['score']:.2f}"
                    + (f" #{d['track_id']}" if "track_id" in d else ""),
                    fill="#facc15",
                    font=font,
                )
                if len(d.get("trail", [])) > 1:
                    draw.line([tuple(p) for p in d["trail"]], fill="#38bdf8", width=2)
        else:
            draw.text(
                (8, 8), result["type"] + " · 详细结果见 JSON / 网页", fill="#facc15", font=font
            )
        image.save(root / "preview.jpg", quality=85)
        # Save per-frame rendering for timeline and downloadable samples; bounded by configured quotas.
        image.save(root / f"result-{index:08d}.jpg", quality=85)

    def cleanup(self):
        # Only generated session artifacts expire; never delete shared input assets or task data.
        for state in self.store.list(limit=10000):
            if (
                state["status"] in TERMINAL
                and state.get("expires_at")
                and state["expires_at"] < now()
                and not state.get("artifacts_expired")
            ):
                shutil.rmtree(self.root / state["id"], ignore_errors=True)
                self.update(state["id"], artifacts={}, bytes=0, artifacts_expired=True)

    def recover(self):
        for state in self.store.list(limit=10000):
            if state["status"] not in TERMINAL:
                self.update(
                    state["id"],
                    status="interrupted",
                    finished_at=now(),
                    error="平台重启，采集已中断；请重新创建会话",
                )

    def shutdown(self):
        for control in list(self.controls.values()):
            control["stop"].set()
        self.executor.shutdown(wait=True, cancel_futures=False)
