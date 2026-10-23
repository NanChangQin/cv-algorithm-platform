"""Opaque project-scoped uploads. Pickle is loaded only inside the training container."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from ...core.errors import ApplicationError

MAX_WEIGHT_BYTES = 2 * 1024**3


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class WeightStorage:
    def __init__(self, root: Path, project_id: UUID):
        self._root = root / "weights" / str(project_id)

    def path(self, weight_id: UUID) -> Path:
        return self._root / str(weight_id) / "model.pt"

    def get(self, weight_id: UUID) -> dict:
        path = self.path(weight_id)
        try:
            metadata = json.loads(path.with_name("metadata.json").read_text(encoding="utf-8"))
            if path.is_file():
                return metadata
        except (OSError, ValueError):
            pass
        raise ApplicationError("WEIGHT_NOT_FOUND", "上传权重不存在", 404)

    def list(self) -> list[dict]:
        items = []
        for path in self._root.glob("*/metadata.json"):
            try:
                items.append(self.get(UUID(path.parent.name)))
            except (ValueError, ApplicationError):
                continue
        return sorted(items, key=lambda item: item["created_at"], reverse=True)

    def upload(self, stream: BinaryIO, filename: str, actor: str) -> dict:
        filename = Path(filename.replace("\\", "/")).name
        if not filename.lower().endswith(".pt"):
            raise ApplicationError("WEIGHT_INVALID", "请选择 Ultralytics 目标检测 .pt 权重", 422)
        weight_id = uuid4()
        path = self.path(weight_id)
        path.parent.mkdir(parents=True)
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("wb") as output:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_WEIGHT_BYTES:
                        raise ApplicationError("WEIGHT_TOO_LARGE", "权重不能超过 2 GiB", 413)
                    output.write(chunk)
                    digest.update(chunk)
            if not size:
                raise ApplicationError("WEIGHT_INVALID", "权重文件为空", 422)
            metadata = {
                "id": str(weight_id), "name": filename[:120], "size_bytes": size,
                "sha256": digest.hexdigest(), "created_by": actor,
                "created_at": datetime.now(UTC).isoformat(),
                "validation": "pending_runtime",
            }
            staging = path.with_name(".metadata.tmp")
            staging.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
            staging.replace(path.with_name("metadata.json"))
            return metadata
        except BaseException:
            shutil.rmtree(path.parent, ignore_errors=True)
            raise

    def delete(self, weight_id: UUID):
        self.get(weight_id)
        shutil.rmtree(self.path(weight_id).parent)


def freeze_weight(source: Path, target: Path, expected: str | None = None) -> str:
    """Each queued run owns its input; deleting the origin cannot break that run."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source, target)
        actual = sha256(target)
        if expected and actual != expected:
            raise ApplicationError("WEIGHT_CORRUPT", "来源权重校验失败，请重新上传或训练", 409)
        return actual
    except FileNotFoundError as exc:
        raise ApplicationError("WEIGHT_NOT_FOUND", "来源权重文件已不存在", 409) from exc
