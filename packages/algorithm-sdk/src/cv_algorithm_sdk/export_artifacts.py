"""Manifest-based exported artifacts; never expose arbitrary runtime output files."""

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

MAX_EXPORT_BYTES = 10 * 1024**3


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def registered_artifacts(root: Path, *, verify_hash: bool = False) -> dict:
    marker = root / "artifact-manifest.json"
    if marker.is_symlink():
        raise ValueError("导出产物清单不能是符号链接")
    if not marker.is_file():
        return {}
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("导出产物清单必须是对象")
    entries = manifest.get("files")
    if manifest.get("version") != 1 or not isinstance(entries, dict) or len(entries) > 2048:
        raise ValueError("导出产物清单格式错误")
    total = 0
    resolved_root = root.resolve()
    for name, item in entries.items():
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or ".." in path.parts
            or any(part.startswith(".") for part in path.parts)
            or name == "artifact-manifest.json"
            or not isinstance(item, dict)
        ):
            raise ValueError("导出产物包含非法路径")
        target = root / name
        if (
            resolved_root not in target.resolve().parents
            or any(
                (root.joinpath(*path.parts[:i])).is_symlink() for i in range(1, len(path.parts) + 1)
            )
            or not target.is_file()
            or type(item.get("size_bytes")) is not int
            or item["size_bytes"] != target.stat().st_size
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256")))
        ):
            raise ValueError("导出产物缺失、大小或哈希格式错误")
        total += item["size_bytes"]
        if total > MAX_EXPORT_BYTES or (verify_hash and digest(target) != item["sha256"]):
            raise ValueError("导出产物超过上限或哈希不一致")
    return entries


def write_manifest(root: Path, names: list[str]) -> None:
    entries = {
        name: {"sha256": digest(root / name), "size_bytes": (root / name).stat().st_size}
        for name in sorted(set(names))
    }
    marker = root / "artifact-manifest.json"
    temporary = root / ".artifact-manifest.tmp"
    temporary.write_text(json.dumps({"version": 1, "files": entries}), encoding="utf-8")
    temporary.replace(marker)
    registered_artifacts(root, verify_hash=True)
