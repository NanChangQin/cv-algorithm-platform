"""Install trusted, locally provisioned YOLO26 weights into a project's model store.

Run as an operator inside the backend container; never loads pickle weights here.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import yaml

from ...core.config import Settings
from ...core.database import Database
from ...core.project_context import DEFAULT_PROJECT_ID
from ..training.publish import inference_manifest
from .domain.entities import AlgorithmVersion
from .infrastructure.model_store import ModelStore
from .infrastructure.postgres_repository import PostgresAlgorithmRepository


def install(repository, store, weights: Path, image: str, project_id: UUID):
    """Idempotent for identical content; refuse to overwrite existing versions."""
    results = []
    paths = [weights / f"yolo26{scale}.pt" for scale in "nsmlx"]
    for path in paths:
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"缺少权重：{path.name}")
    for path in paths:
        key = path.stem
        with path.open("rb") as reader:
            digest = hashlib.file_digest(reader, "sha256").hexdigest()
        existing = next(
            (
                a
                for a in repository.list()
                if a.project_id == project_id
                and a.manifest.id == key
                and a.manifest.version == "1.0.0"
            ),
            None,
        )
        if existing:
            metadata = existing.lineage or {}
            if (
                metadata.get("source_kind") != "pretrained"
                or metadata.get("files", {}).get("model.pt", {}).get("sha256") != digest
            ):
                raise ValueError(f"{key} v1.0.0 已存在且内容不一致，请使用新的模型版本")
            results.append(existing)
            continue
        manifest = inference_manifest(
            key,
            f"YOLO26{key[-1]} · 官方预训练",
            "1.0.0",
            f"Ultralytics YOLO26{key[-1]} COCO 目标检测预训练权重（80 类），可直接推理或微调。",
            640,
        )
        version_id = uuid4()
        try:
            files = store.publish(
                version_id,
                path,
                yaml.safe_dump(
                    manifest.model_dump(mode="json"), allow_unicode=True, sort_keys=False
                ),
            )
            if files["model.pt"]["sha256"] != digest:
                raise ValueError(f"复制期间权重发生变化：{path.name}")
            algorithm = replace(
                AlgorithmVersion.available(
                    manifest,
                    image,
                    algorithm_id=version_id,
                    project_id=project_id,
                    created_by="pretrained-installer",
                ),
                model_directory=f"pretrained/{version_id.hex}",
                lineage={
                    "source_kind": "pretrained",
                    "base_model": path.name,
                    "source_url": f"https://github.com/ultralytics/assets/releases/download/v8.4.0/{path.name}",
                    "files": files,
                },
            )
            repository.add(algorithm)
        except Exception:
            store.delete(version_id)
            raise
        results.append(algorithm)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--project", type=UUID, default=DEFAULT_PROJECT_ID)
    args = parser.parse_args()
    settings = Settings()
    if settings.persistence_backend != "postgres":
        parser.error("安装预训练模型需要 PostgreSQL 持久化配置")
    database = Database.from_settings(settings)
    try:
        for item in install(
            PostgresAlgorithmRepository(database),
            ModelStore(settings.storage_root / "model-store"),
            args.weights,
            settings.yolo_inference_image,
            args.project,
        ):
            print(f"{item.manifest.id} v{item.manifest.version}: {item.id}")
    finally:
        database.close()


if __name__ == "__main__":
    main()
