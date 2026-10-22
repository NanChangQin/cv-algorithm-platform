"""恢复导入算法的 manifest 字段顺序。

迁移 0003 之前 manifest 存在 JSONB 列里，JSONB 会按键长度重排字段，参数表单因此乱序。
导入的算法包解压目录里还保留着原始 manifest.yaml，启动时据此恢复：
只有内容完全一致、仅字段顺序不同的版本才会改写，其他情况一律保持原样。
"""

import json
import logging
from dataclasses import replace
from pathlib import Path

from cv_algorithm_sdk import AlgorithmManifest, load_manifest

from ..domain.repositories import AlgorithmRepository

logger = logging.getLogger(__name__)


def _key_order(manifest: AlgorithmManifest) -> str:
    return json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False)


def restore_manifest_order(repository: AlgorithmRepository) -> int:
    restored = 0
    for algorithm in repository.list():
        if not algorithm.package_path:
            continue
        try:
            original = load_manifest(Path(algorithm.package_path) / "manifest.yaml")
        except Exception:
            # 算法包已被清理或无法解析时保持原样
            continue
        if original != algorithm.manifest or _key_order(original) == _key_order(algorithm.manifest):
            continue
        repository.save(replace(algorithm, manifest=original))
        restored += 1
    if restored:
        logger.info("restored manifest key order", extra={"algorithm_versions": restored})
    return restored
