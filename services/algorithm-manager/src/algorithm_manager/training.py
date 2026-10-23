"""训练任务：在本节点接收数据集、运行训练容器、汇报进度并提供训练产物下载。

节点不保存训练记录，状态全部来自训练容器和运行目录里的文件，Manager 重启后照常可查。
运行目录结构（容器内为 /workspace）：
    config.json      训练配置，由平台下发
    progress.json    训练脚本按批次/轮次写入的进度
    status.json      训练脚本结束时写入的最终状态
    stop_requested   平台请求停止时写入的标记
    train/           Ultralytics 输出目录（weights/best.pt、results.csv 等）
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Any, Literal

from cv_algorithm_sdk.export_artifacts import registered_artifacts
from cv_algorithm_sdk.exports import EXPORT_PROTOCOL, ExportVerification, verification_parameters
from cv_algorithm_sdk.runtime_contract import (
    CONTRACT_ID,
    get_contract,
    normalize_parameters,
    validate_placement,
)
from cv_algorithm_sdk.training_sources import TRAINING_PROTOCOL, TrainingInput
from docker.types import Mount
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from .application.errors import ManagerError
from .infrastructure.docker_runtime import gpu_options

TRAINING_ID_LABEL = "cv.platform.training-id"
MANAGED_TRAINING_LABEL = "cv.platform.managed-training"
IDENTIFIER = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
# 训练产物里可以下载的文件类型
ARTIFACT_SUFFIXES = {".pt", ".csv", ".yaml", ".json", ".png", ".jpg", ".txt"}
ALGORITHM_UID = 10001
# 进度条输出的终端控制序列（清行、颜色等），网页里显示为乱码
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class StartTrainingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["train", "val", "export"] = "train"
    export_verification: ExportVerification | None = None

    training_id: str = Field(pattern=IDENTIFIER)
    dataset_id: str = Field(pattern=IDENTIFIER)
    image: str
    parameters: dict[str, Any]
    training_input: TrainingInput | None = None
    parameter_contract_id: str = CONTRACT_ID
    submitted_parameters: dict[str, Any] | None = None
    gpu_device_ids: list[str] = Field(default_factory=list, max_length=8)
    memory_mb: int | None = Field(default=None, ge=1024, le=1_048_576)

    @field_validator("parameters")
    @classmethod
    def check_parameters(cls, value: dict[str, Any], info) -> dict[str, Any]:
        model = value.get("model")
        if not isinstance(model, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}\.pt", model):
            raise ValueError("model 必须是预置权重文件名")
        return {
            "model": model,
            **normalize_parameters(
                info.data.get("operation", "train"),
                {key: item for key, item in value.items() if key != "model"},
            ),
        }

    @model_validator(mode="after")
    def check_contract(self) -> StartTrainingRequest:
        if self.operation == "export":
            if self.gpu_device_ids or (self.training_input and self.training_input.resume):
                raise ValueError("ONNX 导出使用 CPU，不支持 GPU 或 resume")
            self.export_verification = self.export_verification or ExportVerification()
            verification_parameters(self.parameters, self.export_verification)
        elif self.export_verification is not None:
            raise ValueError("只有导出任务可指定验证策略")
        if self.parameter_contract_id != CONTRACT_ID:
            raise ValueError("训练参数契约不匹配，请更新平台和节点")
        validate_placement(self.parameters, len(self.gpu_device_ids))
        if self.operation == "val" and (
            len(self.gpu_device_ids) != 1 or (self.training_input and self.training_input.resume)
        ):
            raise ValueError("独立验证需要一张 GPU，不能使用 resume")
        if self.operation != "export" and self.training_input and len(self.gpu_device_ids) != 1:
            raise ValueError("自定义权重与断点续训当前需要选择一张 GPU")
        if self.submitted_parameters is not None:
            expected = {key: value for key, value in self.parameters.items() if key != "model"}
            if normalize_parameters(self.operation, self.submitted_parameters) != expected:
                raise ValueError("submitted_parameters 与规范化配置不一致")
        return self

    @field_validator("image")
    @classmethod
    def check_image(cls, value: str) -> str:
        from .api import validate_algorithm_image

        return validate_algorithm_image(value)

    @field_validator("gpu_device_ids")
    @classmethod
    def check_gpus(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[0-9]+", item) for item in value) or len(set(value)) != len(
            value
        ):
            raise ValueError("GPU 编号必须是唯一的非负整数")
        return value


class TrainingService:
    def __init__(
        self,
        client: Any,
        data_root: Path,
        host_data_root: Path,
        *,
        gpu_access_mode: str = "device-request",
        network: str = "none",
        shm_mb: int = 2048,
        max_dataset_bytes: int = 20 * 1024**3,
        max_dataset_files: int = 500_000,
    ) -> None:
        self._client = client
        self._root = data_root / "training"
        self._host_root = host_data_root / "training"
        self._gpu_access_mode = gpu_access_mode
        self._network = network
        self._shm_mb = shm_mb
        self._max_dataset_bytes = max_dataset_bytes
        self._max_dataset_files = max_dataset_files

    # 数据集 ---------------------------------------------------------------

    @property
    def max_dataset_bytes(self) -> int:
        return self._max_dataset_bytes

    def upload_directory(self) -> Path:
        directory = self._root / "uploads"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def dataset_info(self, dataset_id: str) -> dict[str, object] | None:
        marker = self._root / "datasets" / dataset_id / ".sha256"
        if not marker.is_file():
            return None
        return {"dataset_id": dataset_id, "sha256": marker.read_text().strip()}

    def store_dataset(self, dataset_id: str, archive: Path, sha256: str) -> dict[str, object]:
        target = self._root / "datasets" / dataset_id
        current = self.dataset_info(dataset_id)
        if current and current["sha256"] == sha256:
            return current
        staging = target.with_name(f".{dataset_id}.staging")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        try:
            self._extract(archive, staging)
            (staging / ".sha256").write_text(sha256)
            # 训练容器以非 root 用户运行，只读挂载数据集
            for path in [staging, *staging.rglob("*")]:
                path.chmod(0o755 if path.is_dir() else 0o644)
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return {"dataset_id": dataset_id, "sha256": sha256}

    def delete_dataset(self, dataset_id: str) -> None:
        shutil.rmtree(self._root / "datasets" / dataset_id, ignore_errors=True)

    def _extract(self, archive: Path, destination: Path) -> None:
        try:
            with zipfile.ZipFile(archive) as bundle:
                members = bundle.infolist()
                if len(members) > self._max_dataset_files:
                    raise ManagerError("DATASET_INVALID", "数据集文件数量超过节点限制")
                if sum(item.file_size for item in members) > self._max_dataset_bytes:
                    raise ManagerError("DATASET_INVALID", "数据集解压后体积超过节点限制")
                root = destination.resolve()
                for member in members:
                    path = PurePosixPath(member.filename.replace("\\", "/"))
                    file_type = (member.external_attr >> 16) & 0o170000
                    if path.is_absolute() or ".." in path.parts:
                        raise ManagerError("DATASET_INVALID", "数据集包含非法路径")
                    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                        raise ManagerError("DATASET_INVALID", "数据集只能包含普通文件")
                    output = (root / path).resolve()
                    if root not in output.parents:
                        raise ManagerError("DATASET_INVALID", "数据集包含非法路径")
                    if member.is_dir():
                        output.mkdir(parents=True, exist_ok=True)
                        continue
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, output.open("wb") as target:
                        shutil.copyfileobj(source, target, 1024 * 1024)
        except zipfile.BadZipFile as exc:
            raise ManagerError("DATASET_INVALID", "数据集不是有效的 ZIP 文件") from exc

    # 训练 -----------------------------------------------------------------

    def weight_path(self, digest: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ManagerError("WEIGHT_INVALID", "权重校验值格式不正确")
        return self._root / "weights" / f"{digest}.pt"

    def weight_info(self, digest: str):
        path = self.weight_path(digest)
        if not path.is_file() or _sha256(path) != digest:
            return None
        return {"sha256": digest, "size_bytes": path.stat().st_size}

    def store_weight(self, digest: str, temporary: Path):
        target = self.weight_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.chmod(0o644)
        temporary.replace(target)
        return {"sha256": digest, "size_bytes": target.stat().st_size}

    def start(self, request: StartTrainingRequest) -> dict[str, object]:
        training_id = request.training_id
        if self._container(training_id) is not None:
            return self.status(training_id)
        if self.dataset_info(request.dataset_id) is None:
            raise ManagerError("DATASET_MISSING", "节点上没有该训练数据集，请重新提交", 409)
        try:
            image = self._client.images.get(request.image)
        except Exception as exc:
            raise ManagerError("IMAGE_MISSING", f"节点上没有训练镜像 {request.image}", 409) from exc
        if (image.labels or {}).get("cv.platform.parameter-contract") != CONTRACT_ID:
            raise ManagerError(
                "PARAMETER_CONTRACT_MISMATCH", "训练镜像未声明当前参数契约，请重建训练镜像", 409
            )
        if (request.training_input or request.operation == "val") and (
            (image.labels or {}).get("cv.platform.training-protocol") != TRAINING_PROTOCOL
        ):
            raise ManagerError(
                "TRAINING_PROTOCOL_MISMATCH", "训练镜像不支持受管权重与验证，请更新镜像", 409
            )
        if request.operation == "export" and (
            (image.labels or {}).get("cv.platform.export-protocol") != EXPORT_PROTOCOL
        ):
            raise ManagerError("EXPORT_PROTOCOL_MISMATCH", "镜像不支持 ONNX 导出，请重建镜像", 409)
        if request.training_input:
            if self.weight_info(request.training_input.sha256) is None:
                raise ManagerError("WEIGHT_MISSING", "节点权重缺失或损坏，请重新同步", 409)
            if (
                request.training_input.best_sha256
                and self.weight_info(request.training_input.best_sha256) is None
            ):
                raise ManagerError("WEIGHT_MISSING", "原最佳权重缺失或损坏，请重新同步", 409)
            if (
                self.dataset_info(request.dataset_id)["sha256"]
                != request.training_input.dataset_sha256
            ):
                raise ManagerError("DATASET_INVALID", "数据集版本校验不一致", 409)
        run = self._run_dir(training_id)
        shutil.rmtree(run, ignore_errors=True)
        run.mkdir(parents=True)
        gpu_ids = tuple(request.gpu_device_ids)
        config = {
            **request.parameters,
            "operation": request.operation,
            **(
                {"export_verification": request.export_verification.model_dump()}
                if request.operation == "export"
                else {}
            ),
            "parameter_contract_id": request.parameter_contract_id,
            "submitted_parameters": request.submitted_parameters,
            # 容器只看得到分配给它的 GPU，编号从 0 开始
            "device": "cpu"
            if request.operation == "export"
            else (",".join(str(index) for index in range(len(gpu_ids))) if gpu_ids else "0"),
            "dataset": "/dataset",
            "dataset_sha256": self.dataset_info(request.dataset_id)["sha256"],
            "output": "/workspace",
            "training_input": request.training_input.model_dump()
            if request.training_input
            else None,
        }
        (run / "config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(run, ALGORITHM_UID, ALGORITHM_UID)
        else:
            run.chmod(0o777)
        (run / "config.json").chmod(0o644)

        options = (
            {} if request.operation == "export" else gpu_options(self._gpu_access_mode, gpu_ids)
        )
        environment = {
            "PYTHONUNBUFFERED": "1",
            "HOME": "/workspace",
            "YOLO_CONFIG_DIR": "/workspace/.config",
            "MPLCONFIGDIR": "/workspace/.config/matplotlib",
            "YOLO_AUTOINSTALL": "false",
            **options.pop("environment", {}),
        }
        limits = {"mem_limit": f"{request.memory_mb}m"} if request.memory_mb else {}
        inputs = []
        if request.training_input:
            inputs.append(
                Mount(
                    "/input/model.pt",
                    str(self._host_root / "weights" / f"{request.training_input.sha256}.pt"),
                    type="bind",
                    read_only=True,
                )
            )
        if request.training_input and request.training_input.best_sha256:
            inputs.append(
                Mount(
                    "/input/best.pt",
                    str(self._host_root / "weights" / f"{request.training_input.best_sha256}.pt"),
                    type="bind",
                    read_only=True,
                )
            )
        try:
            self._client.containers.run(
                request.image,
                name=f"cv-training-{training_id.replace('-', '')[:12]}",
                detach=True,
                labels={MANAGED_TRAINING_LABEL: "true", TRAINING_ID_LABEL: training_id},
                environment=environment,
                mounts=inputs,
                volumes={
                    str(self._host_root / "datasets" / request.dataset_id): {
                        "bind": "/dataset",
                        "mode": "ro",
                    },
                    str(self._host_root / "runs" / training_id): {
                        "bind": "/workspace",
                        "mode": "rw",
                    },
                },
                network_mode=self._network,
                # DataLoader 多进程读图依赖共享内存，Docker 默认的 64MB 不够
                shm_size=f"{self._shm_mb}m",
                user=f"{ALGORITHM_UID}:{ALGORITHM_UID}",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                **limits,
                **options,
            )
        except Exception as exc:
            raise ManagerError("TRAINING_START_FAILED", f"训练容器启动失败：{exc}", 502) from exc
        return self.status(training_id)

    def status(self, training_id: str) -> dict[str, object]:
        container = self._container(training_id)
        run = self._run_dir(training_id)
        if container is None and not run.exists():
            raise ManagerError("TRAINING_NOT_FOUND", "节点上没有该训练任务", 404)
        final = _read_json(run / "status.json") or {}
        stop_requested = (run / "stop_requested").exists()
        exit_code = None
        error = None
        if container is not None:
            container.reload()
            docker_state = container.status
            exit_code = container.attrs.get("State", {}).get("ExitCode")
        else:
            docker_state = "missing"
        if docker_state == "running":
            state = "stopping" if stop_requested else "running"
        elif docker_state in {"created", "restarting"}:
            state = "starting"
        elif final.get("state") == "completed":
            state = "completed"
        elif stop_requested:
            state = "stopped"
        else:
            state = "failed"
            error = final.get("error") or (
                f"训练进程异常退出（退出码 {exit_code}）"
                if exit_code is not None
                else "训练容器已不存在"
            )
        progress = _read_json(run / "progress.json")
        configuration = _read_json(run / "parameter-resolution.json")
        if configuration is not None:
            progress = {**(progress or {}), "configuration": configuration}
        checkpoint = _read_json(run / "train" / "checkpoint.json")
        if checkpoint is not None:
            progress = {**(progress or {}), "checkpoint": checkpoint}
        return {
            "training_id": training_id,
            "state": state,
            "exit_code": exit_code,
            "error": error,
            "progress": progress,
            "metrics": _read_results(run / "train" / "results.csv"),
            "files": self._artifacts(training_id),
        }

    def logs(self, training_id: str, tail: int) -> list[str]:
        container = self._container(training_id)
        if container is None:
            raise ManagerError("TRAINING_NOT_FOUND", "训练容器已不存在", 404)
        raw = container.logs(tail=min(max(tail, 1), 2000))
        # 进度条用 \r 刷新同一行，只保留每行最后一次刷新的内容（splitlines 会把 \r 也当换行）
        lines = raw.decode("utf-8", "replace").split("\n")
        if lines and not lines[-1]:
            lines.pop()
        return [ANSI.sub("", line.rstrip("\r").rsplit("\r", 1)[-1]) for line in lines]

    def stop(self, training_id: str) -> dict[str, object]:
        container = self._container(training_id)
        if container is None:
            raise ManagerError("TRAINING_NOT_FOUND", "训练容器已不存在", 404)
        (self._run_dir(training_id) / "stop_requested").touch()
        try:
            # 已经保存的 last.pt / best.pt 保留，正在训练的这一轮放弃
            container.stop(timeout=20)
        except Exception as exc:
            raise ManagerError("TRAINING_STOP_FAILED", f"训练容器停止失败：{exc}", 502) from exc
        return self.status(training_id)

    def remove(self, training_id: str) -> None:
        container = self._container(training_id)
        if container is not None:
            container.remove(force=True)
        shutil.rmtree(self._run_dir(training_id), ignore_errors=True)

    def artifact_path(self, training_id: str, name: str) -> Path:
        if name not in self._artifacts(training_id):
            raise ManagerError("ARTIFACT_NOT_FOUND", "训练产物不存在", 404)
        return self._run_dir(training_id) / "train" / name

    def _artifacts(self, training_id: str) -> dict[str, int]:
        output = self._run_dir(training_id) / "train"
        if not output.is_dir():
            return {}
        if (_read_json(self._run_dir(training_id) / "config.json") or {}).get(
            "operation"
        ) == "export":
            try:
                entries = registered_artifacts(output)
                return {
                    **{name: item["size_bytes"] for name, item in entries.items()},
                    **(
                        {
                            "artifact-manifest.json": (output / "artifact-manifest.json")
                            .stat()
                            .st_size
                        }
                        if entries
                        else {}
                    ),
                }
            except (ValueError, OSError, TypeError) as exc:
                raise ManagerError("EXPORT_ARTIFACT_INVALID", str(exc), 409) from exc
        files = {}
        checkpoint = _read_json(output / "checkpoint.json") or {}
        for path in sorted(output.rglob("*")):
            relative = path.relative_to(output)
            if (
                path.is_file()
                and not path.is_symlink()
                and len(relative.parts) <= 2
                and path.suffix.lower() in ARTIFACT_SUFFIXES
                and (path.suffix.lower() != ".txt" or relative.parts[0] == "labels")
                and (
                    not path.name.startswith("resume-")
                    or relative.as_posix() == checkpoint.get("file")
                )
            ):
                files[relative.as_posix()] = path.stat().st_size
        return files

    def _run_dir(self, training_id: str) -> Path:
        return self._root / "runs" / training_id

    def _container(self, training_id: str) -> Any | None:
        found = self._client.containers.list(
            all=True,
            filters={
                "label": [
                    f"{MANAGED_TRAINING_LABEL}=true",
                    f"{TRAINING_ID_LABEL}={training_id}",
                ]
            },
        )
        return found[0] if found else None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _read_results(path: Path) -> list[dict[str, float]]:
    """Ultralytics 每轮追加一行 results.csv，列名带空格对齐，这里统一去掉。"""
    try:
        with path.open(encoding="utf-8", newline="") as source:
            rows = []
            for row in csv.DictReader(source):
                parsed = {}
                for key, value in row.items():
                    try:
                        number = float(value)
                        if math.isfinite(number):
                            parsed[key.strip()] = number
                    except (TypeError, ValueError, AttributeError):
                        continue
                rows.append(parsed)
            return rows
    except OSError:
        return []


def build_training_router(get_training: object) -> APIRouter:
    router = APIRouter(tags=["training"])

    @router.get("/training/parameter-contract")
    def parameter_contract(mode: Literal["train", "val", "export"] = "train"):
        return {
            **get_contract(mode),
            "training_protocol": TRAINING_PROTOCOL,
            "export_protocol": EXPORT_PROTOCOL,
        }

    @router.get("/training/weights/{digest}")
    def weight_info(digest: str, service: TrainingService = Depends(get_training)):
        info = service.weight_info(digest)
        if info is None:
            raise ManagerError("WEIGHT_MISSING", "节点上没有该权重", 404)
        return info

    @router.put("/training/weights/{digest}")
    async def upload_weight(
        digest: str, request: Request, service: TrainingService = Depends(get_training)
    ):
        service.weight_path(digest)
        size = 0
        actual = hashlib.sha256()
        temporary = None
        try:
            with NamedTemporaryFile(
                dir=service.upload_directory(), suffix=".pt", delete=False
            ) as output:
                temporary = Path(output.name)
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 2 * 1024**3:
                        raise ManagerError("WEIGHT_TOO_LARGE", "权重超过 2 GiB", 413)
                    actual.update(chunk)
                    output.write(chunk)
            if not size or actual.hexdigest() != digest:
                raise ManagerError("WEIGHT_INVALID", "权重传输校验失败")
            return await run_in_threadpool(service.store_weight, digest, temporary)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @router.get("/datasets/{dataset_id}")
    def dataset_info(dataset_id: str, service: TrainingService = Depends(get_training)):
        _check_identifier(dataset_id)
        info = service.dataset_info(dataset_id)
        if info is None:
            raise ManagerError("DATASET_MISSING", "节点上没有该训练数据集", 404)
        return info

    @router.put("/datasets/{dataset_id}")
    async def upload_dataset(
        dataset_id: str,
        request: Request,
        service: TrainingService = Depends(get_training),
    ):
        _check_identifier(dataset_id)
        expected = request.headers.get("X-Content-SHA256", "").lower()
        if len(expected) != 64:
            raise ManagerError("DATASET_INVALID", "缺少数据集校验值 X-Content-SHA256")
        digest = hashlib.sha256()
        size = 0
        uploads = service.upload_directory()
        with NamedTemporaryFile(dir=uploads, suffix=".zip", delete=False) as target:
            archive = Path(target.name)
            try:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > service.max_dataset_bytes:
                        raise ManagerError("DATASET_TOO_LARGE", "数据集超过节点大小限制", 413)
                    digest.update(chunk)
                    target.write(chunk)
            except BaseException:
                archive.unlink(missing_ok=True)
                raise
        try:
            if digest.hexdigest() != expected:
                raise ManagerError("DATASET_INVALID", "数据集传输不完整，校验值不一致")
            return await run_in_threadpool(service.store_dataset, dataset_id, archive, expected)
        finally:
            archive.unlink(missing_ok=True)

    @router.delete("/datasets/{dataset_id}", status_code=204)
    def delete_dataset(dataset_id: str, service: TrainingService = Depends(get_training)):
        _check_identifier(dataset_id)
        service.delete_dataset(dataset_id)

    @router.post("/trainings")
    def start_training(
        payload: StartTrainingRequest,
        service: TrainingService = Depends(get_training),
    ):
        return service.start(payload)

    @router.get("/trainings/{training_id}")
    def training_status(training_id: str, service: TrainingService = Depends(get_training)):
        _check_identifier(training_id)
        return service.status(training_id)

    @router.get("/trainings/{training_id}/logs", response_model=list[str])
    def training_logs(
        training_id: str,
        tail: int = 300,
        service: TrainingService = Depends(get_training),
    ):
        _check_identifier(training_id)
        return service.logs(training_id, tail)

    @router.post("/trainings/{training_id}/stop")
    def stop_training(training_id: str, service: TrainingService = Depends(get_training)):
        _check_identifier(training_id)
        return service.stop(training_id)

    @router.delete("/trainings/{training_id}", status_code=204)
    def remove_training(training_id: str, service: TrainingService = Depends(get_training)):
        _check_identifier(training_id)
        service.remove(training_id)

    @router.get("/trainings/{training_id}/files/{name:path}")
    def download_artifact(
        training_id: str,
        name: str,
        service: TrainingService = Depends(get_training),
    ):
        _check_identifier(training_id)
        return FileResponse(service.artifact_path(training_id, name))

    return router


def _check_identifier(value: str) -> None:
    if not re.fullmatch(IDENTIFIER, value):
        raise ManagerError("INPUT_INVALID", "ID 格式不正确")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
