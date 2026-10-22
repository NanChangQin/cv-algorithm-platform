"""主服务器上保存的模型文件：由训练结果发布的算法版本，权重和 manifest 都放在这里，
部署或推理时再同步到运行节点的模型目录。"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

from ...instances.domain.models import ModelFile
from ..domain.entities import AlgorithmVersion


class ModelStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def directory(self, version_id: UUID) -> Path:
        return self._root / str(version_id)

    def publish(
        self, version_id: UUID, weights: Path, manifest_yaml: str
    ) -> dict[str, dict[str, Any]]:
        """复制权重、写入 manifest，返回每个文件的大小和 SHA-256。"""
        target = self.directory(version_id)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(weights, target / "model.pt")
        (target / "manifest.yaml").write_text(manifest_yaml, encoding="utf-8")
        return {
            name: {"size": (target / name).stat().st_size, "sha256": _sha256(target / name)}
            for name in ("model.pt", "manifest.yaml")
        }

    def files(self, algorithm: AlgorithmVersion) -> tuple[ModelFile, ...]:
        recorded = (algorithm.lineage or {}).get("files") or {}
        return tuple(
            ModelFile(name, self.directory(algorithm.id) / name, str(item["sha256"]),
                      int(item["size"]))
            for name, item in recorded.items()
        )

    def delete(self, version_id: UUID) -> None:
        shutil.rmtree(self.directory(version_id), ignore_errors=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while chunk := reader.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
