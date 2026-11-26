"""Project-scoped batch jobs. Results survive refresh; interrupted jobs can be retried."""

from __future__ import annotations

import fcntl
import json
import logging
import re
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from threading import BoundedSemaphore, RLock
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...core.errors import ApplicationError
from .api import get_service
from .reports import csv_bytes, json_bytes, pdf_report, snapshot, write_bundle
from .service import TrainingService

router = APIRouter(prefix="/training/batches", tags=["training"])
logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="training-batch")
_slots = BoundedSemaphore(4)
_lock = RLock()
_process = uuid4().hex
TTL = 24 * 3600


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_ids: list[UUID] = Field(min_length=1, max_length=1000)

    @field_validator("run_ids")
    @classmethod
    def unique(cls, value):
        return list(dict.fromkeys(value))


class BatchRequest(Selection):
    request_id: UUID
    action: Literal["download", "delete"]
    content: Literal["weights", "reports", "metrics", "all"] = "all"


def root(service):
    return service._storage.root / "batches" / str(service._project_id)


@contextmanager
def creation_lock(service):
    root(service).mkdir(parents=True, exist_ok=True)
    with (root(service) / ".creation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def recover_interrupted(directory, state):
    if state["status"] not in {"queued", "running"}:
        return
    # A filesystem lock survives requests and is visible to every backend process.
    # The OS releases it on process death, unlike a process-local task registry.
    with (directory / ".running.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            state["status"] = "failed"
            state["error"] = "服务重启导致操作中断，请重试未完成项"
            state["finished_at"] = time.time()
            write_state(directory, state)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def write_state(directory, state):
    directory.mkdir(parents=True, exist_ok=True)
    staging = directory / f".{uuid4().hex}.json"
    staging.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    staging.replace(directory / "state.json")


def read_state(service, job_id):
    directory = root(service) / str(job_id)
    with _lock:
        try:
            state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ApplicationError("BATCH_NOT_FOUND", "批量任务不存在或已过期", 404) from exc
        if state["actor"] != service._actor:
            raise ApplicationError("BATCH_NOT_FOUND", "批量任务不存在或已过期", 404)
        recover_interrupted(directory, state)
        if time.time() - (state.get("finished_at") or time.time()) > TTL:
            raise ApplicationError("BATCH_EXPIRED", "结果已过期，请重新打包", 410)
        return state


def public(state):
    return {key: value for key, value in state.items() if key not in {"actor", "process"}}


def delete_preview(service, ids):
    items = []
    for run_id in ids:
        item = {"id": str(run_id), "name": str(run_id), "eligible": False, "reason": ""}
        try:
            run = service.get_run(run_id)
            item["name"] = run.name
            service.check_delete(run_id)
            item["eligible"] = True
        except ApplicationError as exc:
            item["reason"] = exc.message
        items.append(item)
    return items


@router.post("/delete-preview")
def preview(payload: Selection, service: TrainingService = Depends(get_service)):
    return {"items": delete_preview(service, payload.run_ids)}


@router.post("", status_code=202)
def create(payload: BatchRequest, service: TrainingService = Depends(get_service)):
    directory = root(service) / str(payload.request_id)
    request = payload.model_dump(mode="json", exclude={"request_id"})
    with _lock, creation_lock(service):
        if (directory / "state.json").exists():
            existing = read_state(service, payload.request_id)
            if existing["request"] != request:
                raise ApplicationError("BATCH_CONFLICT", "此请求已用于另一批操作", 409)
            return public(existing)
        # Validate project access before scheduling any work, including downloads.
        for run_id in payload.run_ids:
            try:
                service.get_run(run_id)
            except ApplicationError:
                # A prior interrupted delete may have already removed this task.
                if payload.action != "delete" or service._repository.get_run(run_id) is not None:
                    raise
        if not _slots.acquire(blocking=False):
            raise ApplicationError("BATCH_BUSY", "批量操作较多，请稍后重试", 429)
        job_lock = None
        try:
            # Completed packages expire after 24 hours; remove on the next job creation.
            for path in root(service).glob("*/state.json"):
                try:
                    old = json.loads(path.read_text(encoding="utf-8"))
                    if old.get("finished_at") and time.time() - old["finished_at"] > TTL:
                        shutil.rmtree(path.parent)
                except (OSError, ValueError):
                    continue
            state = {
                "id": str(payload.request_id),
                "actor": service._actor,
                "process": _process,
                "request": request,
                "status": "queued",
                "total": len(payload.run_ids),
                "processed": 0,
                "current": "",
                "items": [],
                "error": "",
                "download_ready": False,
                "created_at": time.time(),
                "finished_at": None,
            }
            directory.mkdir(parents=True, exist_ok=True)
            job_lock = (directory / ".running.lock").open("a")
            fcntl.flock(job_lock, fcntl.LOCK_EX)
            write_state(directory, state)
            _executor.submit(execute, service, directory, state, job_lock)
        except Exception:
            if job_lock is not None:
                job_lock.close()
            _slots.release()
            raise
        return public(state.copy())


@router.get("/{job_id}")
def get(job_id: UUID, service: TrainingService = Depends(get_service)):
    return public(read_state(service, job_id))


@router.get("/{job_id}/download")
def download(job_id: UUID, service: TrainingService = Depends(get_service)):
    state = read_state(service, job_id)
    path = root(service) / str(job_id) / "results.zip"
    if not state["download_ready"] or not path.is_file():
        raise ApplicationError("BATCH_NOT_READY", "下载包尚未准备好", 409)
    return FileResponse(
        path, media_type="application/zip", filename=f"training-{job_id.hex[:8]}.zip"
    )


def package_task(service, run, content, directory):
    """Create each task separately so a missing file cannot corrupt other tasks."""
    path = directory / f"{run.id}.zip"
    for name in run.artifacts:
        if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or "\\" in name:
            raise ApplicationError("ARTIFACT_INVALID", "产物路径无效，无法打包", 409)
    if content == "all":
        write_bundle(service, run, path)
        return path
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        if content == "weights":
            names = [name for name in run.artifacts if Path(name).suffix.lower() in {".pt", ".pth"}]
            if not names:
                raise ApplicationError("WEIGHTS_MISSING", "尚无模型权重", 409)
            for name in names:
                archive.write(
                    service.artifact(run.id, name), name, compress_type=zipfile.ZIP_STORED
                )
        else:
            report = snapshot(service, run)
            if content == "reports":
                archive.writestr("report.pdf", pdf_report(service, run, report))
                archive.writestr("report.json", json_bytes(report))
            else:
                classes = (report["evaluation"] or {}).get("per_class", [])
                if not report["metrics"] and not classes:
                    raise ApplicationError("METRICS_MISSING", "尚无指标数据", 409)
                if report["metrics"]:
                    archive.writestr("metrics.csv", csv_bytes(report["metrics"]))
                if classes:
                    archive.writestr("per-class.csv", csv_bytes(classes))
    return path


def execute(service, directory, state, job_lock):
    archive = None
    try:
        state["status"] = "running"
        write_state(directory, state)
        if state["request"]["action"] == "download":
            archive = zipfile.ZipFile(directory / "results.partial", "w", allowZip64=True)
        for identifier in state["request"]["run_ids"]:
            run_id = UUID(identifier)
            item = {"id": identifier, "name": identifier, "status": "failed", "reason": ""}
            task_zip = directory / f"{run_id}.zip"
            try:
                run = service.get_run(run_id)
                item["name"] = run.name
                state["current"] = run.name
                write_state(directory, state)
                if archive is None:
                    service.delete_run(run_id)
                else:
                    task_zip = package_task(service, run, state["request"]["content"], directory)
                    name = (
                        re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", run.name).strip(". ")[:64] or "训练"
                    )
                    folder = f"{name}-{run.id.hex[:12]}"
                    with zipfile.ZipFile(task_zip) as source:
                        for info in source.infolist():
                            # Stream weights into task folders without loading them into RAM.
                            target = zipfile.ZipInfo(f"{folder}/{info.filename}")
                            target.compress_type = info.compress_type
                            with (
                                source.open(info) as src,
                                archive.open(target, "w", force_zip64=True) as dst,
                            ):
                                shutil.copyfileobj(src, dst, 1024 * 1024)
                item["status"] = "success"
            except ApplicationError as exc:
                item.update(
                    status="skipped" if exc.status_code in {404, 409} else "failed",
                    reason=exc.message,
                )
            except Exception:
                logger.exception("Batch task failed: %s", identifier)
                item["reason"] = "操作失败，请重试；若持续失败请检查服务日志"
            finally:
                task_zip.unlink(missing_ok=True)
            state["items"].append(item)
            state["processed"] += 1
            write_state(directory, state)
        if archive is not None:
            archive.writestr("操作结果.json", json_bytes(state["items"]))
            archive.close()
            archive = None
            (directory / "results.partial").replace(directory / "results.zip")
            state["download_ready"] = True
        state["status"] = "completed"
    except Exception:
        logger.exception("Batch operation failed: %s", state["id"])
        state["status"] = "failed"
        state["error"] = "批量操作中断，请重试未完成项"
    finally:
        if archive is not None:
            archive.close()
        (directory / "results.partial").unlink(missing_ok=True)
        state["current"] = ""
        state["finished_at"] = time.time()
        try:
            write_state(directory, state)
        finally:
            job_lock.close()
            _slots.release()
