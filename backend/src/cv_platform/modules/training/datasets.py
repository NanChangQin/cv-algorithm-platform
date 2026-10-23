"""YOLO 目标检测数据集的导入、校验和规整。

接受常见的几种目录结构（Ultralytics / Roboflow 导出、只有 images 和 labels 的平铺目录），
统一整理成下面的结构再保存，训练节点和训练脚本只需处理这一种：

    data.yaml            names: [类别名, ...]
    images/train/*.jpg   labels/train/*.txt
    images/val/*.jpg     labels/val/*.txt
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import shutil
import stat
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO, Literal

import yaml
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...core.errors import ApplicationError

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
SPLITS = {"train": "train", "training": "train", "val": "val", "valid": "val",
          "validation": "val", "test": "test"}
SPLIT_DIRECTORY = re.compile(r"(training|train|validation|valid|val|test)[-_]?\d{0,4}")
YAML_NAMES = {"data.yaml", "data.yml", "dataset.yaml", "dataset.yml"}
NAME_FILES = {"classes.txt", "obj.names", "classes.names"}
MAX_TEXT_BYTES = 1024 * 1024
MAX_EXAMPLES = 8


class SplitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["preserve", "reshuffle"] = "preserve"
    train: float = Field(default=0.8, gt=0, lt=1, allow_inf_nan=False, strict=True)
    val: float = Field(default=0.2, gt=0, lt=1, allow_inf_nan=False, strict=True)
    test: float = Field(default=0, ge=0, lt=1, allow_inf_nan=False, strict=True)
    seed: int = Field(default=0, ge=0, le=2**32 - 1, strict=True)
    validation: Literal["repair", "strict"] = "repair"

    @model_validator(mode="after")
    def ratios(self) -> SplitConfig:
        if not math.isclose(self.train + self.val + self.test, 1, abs_tol=1e-8):
            raise ValueError("train / val / test 比例之和必须为 1")
        return self


@dataclass(frozen=True, slots=True)
class ImportedDataset:
    class_names: tuple[str, ...]
    stats: dict[str, Any]
    size_bytes: int
    sha256: str
    source_sha256: str


@dataclass(slots=True)
class _Image:
    member: zipfile.ZipInfo
    path: PurePosixPath
    split: str | None
    label: zipfile.ZipInfo | None = None


def _fail(message: str) -> ApplicationError:
    return ApplicationError("DATASET_INVALID", message)


class TrainingDatasetStorage:
    def __init__(self, root: Path, max_bytes: int, max_files: int) -> None:
        self._root = root.resolve()
        self._max_bytes = max_bytes
        self._max_files = max_files

    def path(self, dataset_id: object) -> Path:
        return self._root / "datasets" / f"{dataset_id}.zip"

    def run_directory(self, run_id: object) -> Path:
        return self._root / "runs" / str(run_id)

    @property
    def root(self) -> Path:
        return self._root

    def source_path(self, dataset_id: object) -> Path:
        return self.path(dataset_id).with_suffix(".source.zip")

    def index_path(self, dataset_id: object) -> Path:
        return self.path(dataset_id).with_suffix(".index.json")

    def delete(self, dataset_id: object) -> None:
        self.path(dataset_id).unlink(missing_ok=True)
        self.source_path(dataset_id).unlink(missing_ok=True)
        self.index_path(dataset_id).unlink(missing_ok=True)

    def delete_run(self, run_id: object) -> None:
        shutil.rmtree(self.run_directory(run_id), ignore_errors=True)

    def index(self, dataset_id: object) -> dict[str, Any]:
        cached = self.index_path(dataset_id)
        if cached.is_file():
            return json.loads(cached.read_text(encoding="utf-8"))
        # 历史版本没有索引：只读原规范化包，不更改历史图片、划分或内容哈希。
        if not self.path(dataset_id).is_file():
            raise ApplicationError("DATASET_FILE_NOT_FOUND", "数据集文件不存在", 404)
        entries = []
        with zipfile.ZipFile(self.path(dataset_id)) as archive:
            names = set(archive.namelist())
            for name in sorted(names):
                path = PurePosixPath(name)
                if path.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                label = str(path).replace("images/", "labels/", 1)
                label = str(PurePosixPath(label).with_suffix(".txt"))
                lines = _parse_label(archive, archive.getinfo(label))[0] if label in names else []
                try:
                    metadata = _inspect_image(archive, archive.getinfo(name))
                except ApplicationError:
                    metadata = {"width": 0, "height": 0, "sha256": None}
                entries.append({"id": len(entries), "path": name, "source_path": name,
                                "split": path.parts[1], **metadata,
                                "boxes": [_box(line) for line in lines]})
        return {"schema_version": 0, "images": entries, "invalid_labels": [],
                "orphan_labels": [], "duplicates": [], "leakage": []}

    def preview(self, dataset_id: object, image_id: int) -> bytes:
        if not self.path(dataset_id).is_file():
            raise ApplicationError("DATASET_FILE_NOT_FOUND", "数据集文件不存在", 404)
        with zipfile.ZipFile(self.path(dataset_id)) as archive:
            # 新索引的 id 与规范化 ZIP 的图片写入顺序一致，无须每张预览加载整个 JSON。
            names = [n for n in archive.namelist()
                     if PurePosixPath(n).suffix.lower() in IMAGE_SUFFIXES]
            if not self.index_path(dataset_id).is_file():
                names.sort()  # 历史版本只读索引按路径排序
            if not 0 <= image_id < len(names):
                raise ApplicationError("DATASET_IMAGE_NOT_FOUND", "图片不存在", 404)
            try:
                with archive.open(names[image_id]) as stream, Image.open(stream) as image:
                    if image.width * image.height > 40_000_000:
                        raise ValueError("image too large")
                    image.thumbnail((1280, 1280))
                    result = io.BytesIO()
                    image.convert("RGB").save(result, "JPEG", quality=85)
                    return result.getvalue()
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise _fail("图片损坏或无法预览") from exc

    def import_archive(
        self, stream: BinaryIO, filename: str, dataset_id: object,
        config: SplitConfig | None = None,
    ) -> ImportedDataset:
        if not filename.lower().endswith(".zip"):
            raise _fail("请上传 ZIP 格式的数据集")
        target = self.path(dataset_id)
        if target.exists() or self.source_path(dataset_id).exists():
            raise ApplicationError("DATASET_IMMUTABLE", "数据集版本不能覆盖", 409)
        target.parent.mkdir(parents=True, exist_ok=True)
        upload = self._receive(stream, target.parent)
        try:
            with zipfile.ZipFile(upload) as source:
                imported = self._normalize(source, target, config or SplitConfig())
            source_hash = _hash_file(upload)
            upload.replace(self.source_path(dataset_id))
            return ImportedDataset(
                imported.class_names, imported.stats, imported.size_bytes,
                imported.sha256, source_hash,
            )
        except zipfile.BadZipFile as exc:
            self.delete(dataset_id)
            raise _fail("数据集不是有效的 ZIP 文件") from exc
        except Exception:
            self.delete(dataset_id)
            raise
        finally:
            upload.unlink(missing_ok=True)

    def _receive(self, stream: BinaryIO, directory: Path) -> Path:
        size = 0
        with NamedTemporaryFile(dir=directory, suffix=".upload", delete=False) as target:
            path = Path(target.name)
            try:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise ApplicationError("DATASET_TOO_LARGE", "数据集超过大小限制", 413)
                    target.write(chunk)
            except BaseException:
                target.close()
                path.unlink(missing_ok=True)
                raise
        return path

    def _normalize(
        self, source: zipfile.ZipFile, target: Path, config: SplitConfig,
    ) -> ImportedDataset:
        members = self._safe_members(source)
        images: list[_Image] = []
        labels: dict[PurePosixPath, zipfile.ZipInfo] = {}
        yaml_files: list[tuple[PurePosixPath, zipfile.ZipInfo]] = []
        name_files: list[tuple[PurePosixPath, zipfile.ZipInfo]] = []
        skipped = 0
        for path, member in members:
            suffix = path.suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                split = next((found for part in path.parts[:-1]
                              if (found := _split_of(part))), None)
                images.append(_Image(member, path, split))
            elif suffix == ".txt" and path.name.lower() not in NAME_FILES:
                labels[path] = member
            elif path.name.lower() in YAML_NAMES:
                yaml_files.append((path, member))
            elif path.name.lower() in NAME_FILES:
                name_files.append((path, member))
            else:
                skipped += 1
        if not images:
            raise _fail("数据集中没有找到图片（支持 jpg、png、bmp、webp、tif）")
        images.sort(key=lambda image: str(image.path))
        inspected = [_inspect_image(source, image.member) for image in images]

        used_labels: set[PurePosixPath] = set()
        for image in images:
            for candidate in _label_candidates(image.path):
                if candidate in labels:
                    image.label = labels[candidate]
                    used_labels.add(candidate)
                    break

        warnings: list[str] = []
        class_names = self._class_names(source, yaml_files, name_files)
        splits, auto_split = _assign_splits(images, inspected, warnings, config)

        parsed: dict[int, tuple[list[str], Counter[int]]] = {}
        invalid_lines = 0
        invalid_examples: list[str] = []
        audit: list[str] = []
        max_class = -1
        for index, image in enumerate(images):
            if image.label is None or splits[index] is None:
                continue
            lines, counts, problems = _parse_label(source, image.label)
            invalid_lines += len(problems)
            audit.extend(problems)
            invalid_examples.extend(problems[: MAX_EXAMPLES - len(invalid_examples)])
            if counts:
                max_class = max(max_class, max(counts))
            parsed[index] = (lines, counts)

        if class_names is None:
            if max_class < 0:
                raise _fail("没有找到类别名称，也没有任何标注框：请在 data.yaml 中提供 names")
            class_names = tuple(f"class_{index}" for index in range(max_class + 1))
            warnings.append("没有找到 data.yaml 或 classes.txt，类别名称按编号自动生成")
        elif max_class >= len(class_names):
            raise _fail(
                f"标注中出现了类别编号 {max_class}，但只定义了 {len(class_names)} 个类别名称"
            )

        train_boxes = sum(
            sum(parsed[index][1].values())
            for index, split in enumerate(splits) if split == "train" and index in parsed
        )
        if train_boxes == 0:
            raise _fail("训练集中没有任何标注框，请检查 labels 目录是否与 images 对应")

        # labels 目录外没用上的 txt（README 等）不算标注文件
        unused = [path for path in labels if path not in used_labels]
        orphan_labels = sum("labels" in (part.lower() for part in path.parts) for path in unused)
        skipped += len(unused) - orphan_labels
        if orphan_labels:
            warnings.append(f"{orphan_labels} 个标注文件找不到对应图片，已忽略")
        if invalid_lines:
            if config.validation == "strict":
                raise _fail(f"严格校验失败：{invalid_lines} 行错误标注；{invalid_examples[0]}")
            warnings.append(f"{invalid_lines} 行标注格式不正确，已删除")
        if orphan_labels and config.validation == "strict":
            raise _fail(f"严格校验失败：{orphan_labels} 个孤立标注文件")
        test_images = sum(split == "test" for split in splits)
        if test_images:
            warnings.append(f"测试集 {test_images} 张图片不参与训练")

        stats, entries = self._write(source, target, images, splits, parsed, class_names, inspected)
        groups: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            groups.setdefault(entry["sha256"], []).append(entry)
        duplicates = [group for group in groups.values() if len(group) > 1]
        leakage = [group for group in duplicates if len({e["split"] for e in group}) > 1]
        if duplicates:
            warnings.append(f"发现 {len(duplicates)} 组内容完全相同的图片")
        if leakage:
            warnings.append(f"发现 {len(leakage)} 组跨划分重复图片，存在数据泄漏风险")
        stats.update({
            "auto_split": auto_split,
            "warnings": warnings,
            "invalid_lines": invalid_lines,
            "invalid_examples": invalid_examples,
            "orphan_labels": orphan_labels,
            "skipped_files": skipped,
            "duplicate_groups": len(duplicates),
            "duplicate_images": sum(len(group) - 1 for group in duplicates),
            "leakage_groups": len(leakage),
        })
        target.with_suffix(".index.json").write_text(json.dumps({
            "schema_version": 1, "images": entries, "invalid_labels": audit,
            "orphan_labels": [str(path) for path in unused
                              if "labels" in (part.lower() for part in path.parts)],
            "duplicates": [[e["id"] for e in group] for group in duplicates],
            "leakage": [[e["id"] for e in group] for group in leakage],
        }, ensure_ascii=False), encoding="utf-8")
        return ImportedDataset(class_names, stats, target.stat().st_size, _hash_file(target), "")

    def _safe_members(self, source: zipfile.ZipFile) -> list[tuple[PurePosixPath, zipfile.ZipInfo]]:
        infos = source.infolist()
        if len(infos) > self._max_files:
            raise _fail(f"数据集文件数量超过 {self._max_files} 个")
        if sum(item.file_size for item in infos) > self._max_bytes * 2:
            raise _fail("数据集解压后体积过大")
        members = []
        for info in infos:
            path = PurePosixPath(info.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts:
                raise _fail("数据集包含非法路径")
            if ((info.external_attr >> 16) & 0o170000) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise _fail("数据集只能包含普通文件和目录")
            if info.is_dir() or any(
                part.startswith(".") or part == "__MACOSX" for part in path.parts
            ):
                continue
            members.append((path, info))
        # 压缩时多套了一层目录（dataset/images/...）的情况
        tops = {path.parts[0] for path, _ in members if len(path.parts) > 1}
        if len(tops) == 1 and all(len(path.parts) > 1 for path, _ in members):
            members = [(PurePosixPath(*path.parts[1:]), info) for path, info in members]
        paths = [str(path).casefold() for path, _ in members]
        if len(set(paths)) != len(paths):
            raise _fail("数据集包含重复路径（忽略大小写），请先去重")
        return sorted(members, key=lambda item: str(item[0]))

    @staticmethod
    def _class_names(
        source: zipfile.ZipFile,
        yaml_files: list[tuple[PurePosixPath, zipfile.ZipInfo]],
        name_files: list[tuple[PurePosixPath, zipfile.ZipInfo]],
    ) -> tuple[str, ...] | None:
        for _, member in sorted(yaml_files, key=lambda item: len(item[0].parts)):
            if member.file_size > MAX_TEXT_BYTES:
                continue
            try:
                data = yaml.safe_load(source.read(member).decode("utf-8-sig"))
            except (yaml.YAMLError, UnicodeDecodeError) as exc:
                raise _fail(f"{member.filename} 不是有效的 YAML") from exc
            names = data.get("names") if isinstance(data, dict) else None
            if isinstance(names, list) and names:
                return tuple(str(name) for name in names)
            if isinstance(names, dict) and names:
                try:
                    indexed = {int(key): str(value) for key, value in names.items()}
                except ValueError as exc:
                    raise _fail("data.yaml 的 names 编号必须是整数") from exc
                if sorted(indexed) != list(range(len(indexed))):
                    raise _fail("data.yaml 的 names 编号必须从 0 开始连续")
                return tuple(indexed[index] for index in range(len(indexed)))
        for _, member in sorted(name_files, key=lambda item: len(item[0].parts)):
            if member.file_size > MAX_TEXT_BYTES:
                continue
            lines = source.read(member).decode("utf-8-sig", "replace").splitlines()
            names = tuple(line.strip() for line in lines if line.strip())
            if names:
                return names
        return None

    @staticmethod
    def _write(
        source: zipfile.ZipFile,
        target: Path,
        images: list[_Image],
        splits: list[str | None],
        parsed: dict[int, tuple[list[str], Counter[int]]],
        class_names: tuple[str, ...],
        inspected: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        summary = {
            split: {"images": 0, "labeled": 0, "backgrounds": 0, "boxes": 0}
            for split in ("train", "val", "test")
        }
        instances: Counter[int] = Counter()
        used_names: dict[str, set[str]] = {split: set() for split in summary}
        entries: list[dict[str, Any]] = []
        split_instances = {split: Counter() for split in summary}
        staging = target.with_name(f".{target.name}.staging")
        try:
            with zipfile.ZipFile(staging, "w") as output:
                output.writestr(
                    _zip_info("data.yaml", zipfile.ZIP_DEFLATED),
                    yaml.safe_dump({"names": list(class_names), "nc": len(class_names),
                                    **{s: f"images/{s}" for s in summary if s in splits}},
                                   allow_unicode=True),
                    compress_type=zipfile.ZIP_DEFLATED,
                )
                for index, image in enumerate(images):
                    split = splits[index]
                    if split not in summary:
                        continue
                    stem = _unique_stem(image.path, used_names[split])
                    name = f"{stem}{image.path.suffix.lower()}"
                    # 图片本身已压缩，直接存储更快
                    with source.open(image.member) as reader, output.open(
                        _zip_info(f"images/{split}/{name}"), "w"
                    ) as writer:
                        shutil.copyfileobj(reader, writer, 1024 * 1024)
                    counts = summary[split]
                    counts["images"] += 1
                    lines, boxes = parsed.get(index, ([], Counter()))
                    entries.append({
                        "id": len(entries), "path": f"images/{split}/{name}",
                        "source_path": str(image.path), "split": split,
                        **inspected[index], "boxes": [_box(line) for line in lines],
                    })
                    split_instances[split].update(boxes)
                    if lines:
                        counts["labeled"] += 1
                        counts["boxes"] += sum(boxes.values())
                        instances.update(boxes)
                        output.writestr(
                            _zip_info(f"labels/{split}/{stem}.txt", zipfile.ZIP_DEFLATED),
                            "\n".join(lines) + "\n",
                            compress_type=zipfile.ZIP_DEFLATED,
                        )
                    else:
                        counts["backgrounds"] += 1
            staging.replace(target)
        finally:
            staging.unlink(missing_ok=True)
        return {
            "splits": summary,
            "instances": {class_names[key]: instances[key] for key in range(len(class_names))},
            "split_instances": {s: {str(k): c[k] for k in range(len(class_names))}
                                for s, c in split_instances.items()},
        }, entries


def _split_of(directory: str) -> str | None:
    """train、valid、train2017、val_2 这类目录名对应的划分。"""
    match = SPLIT_DIRECTORY.fullmatch(directory.lower())
    return SPLITS[match.group(1)] if match else None


def _label_candidates(image: PurePosixPath) -> list[PurePosixPath]:
    """Ultralytics 的约定：把路径里最后一个 images 换成 labels；其次是同目录的同名 txt。"""
    candidates = []
    parts = list(image.parts)
    for position in range(len(parts) - 2, -1, -1):
        if parts[position].lower() == "images":
            replaced = [*parts[:position], "labels", *parts[position + 1:]]
            candidates.append(PurePosixPath(*replaced).with_suffix(".txt"))
            break
    candidates.append(image.with_suffix(".txt"))
    return candidates


def _assign_splits(
    images: list[_Image], inspected: list[dict[str, Any]],
    warnings: list[str], config: SplitConfig,
) -> tuple[list[str], bool]:
    splits = [image.split or "train" for image in images]
    if config.mode == "reshuffle":
        pool = list(range(len(images)))
        ratios = {"train": config.train, "val": config.val, "test": config.test}
    elif "val" in splits:
        if "train" not in splits:
            raise _fail("只有验证集，没有训练集")
        return splits, False
    else:
        # 独立 test 永远不参加缺失 val 的自动划分。
        pool = [i for i, split in enumerate(splits) if split == "train"]
        ratios = {"train": config.train, "val": config.val}
    groups: dict[str, list[int]] = {}
    for i in pool:
        groups.setdefault(inspected[i]["sha256"], []).append(i)
    active = [s for s, ratio in ratios.items() if ratio > 0]
    if len(groups) < len(active):
        raise _fail(f"至少需要 {len(active)} 组不同内容的图片才能划分；重复图片始终分在一起")
    # 按内容分组后稳定排序；ZIP 顺序、时间戳和包装目录不影响划分。
    ordered = sorted(groups, key=lambda sha: hashlib.sha256(
        f"{config.seed}:{sha}".encode()).hexdigest())
    total = sum(ratios.values())
    targets = {s: len(pool) * ratios[s] / total for s in active}
    assigned = Counter()
    for position, sha in enumerate(ordered):
        remaining = len(ordered) - position
        empty = [s for s in active if not assigned[s]]
        split = empty[0] if remaining == len(empty) else max(
            active, key=lambda s: targets[s] - assigned[s])
        for i in groups[sha]:
            splits[i] = split
        assigned[split] += len(groups[sha])
    warnings.append("已按内容分组和固定随机种子划分，重复图片保留在同一划分")
    return splits, True


def _hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _zip_info(name: str, compression: int = zipfile.ZIP_STORED) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compression
    info.external_attr = 0o100644 << 16
    return info


def _inspect_image(source: zipfile.ZipFile, member: zipfile.ZipInfo) -> dict[str, Any]:
    try:
        with source.open(member) as stream, Image.open(stream) as image:
            width, height = image.size
            if width * height > 40_000_000 or min(width, height) < 1:
                raise _fail(f"{member.filename}: 图片尺寸超过限制（4000 万像素）")
            image.verify()
        with source.open(member) as stream, Image.open(stream) as image:
            # EXIF 旋转会改变标注坐标解释，要求上传前规范化。
            if image.getexif().get(274, 1) not in {0, 1}:
                raise _fail(f"{member.filename}: 请先应用 EXIF 旋转并同步标注坐标")
            image.load()  # verify() 不解码 JPEG，损坏像素也需在导入时暴露。
        with source.open(member) as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return {"width": width, "height": height, "sha256": digest}
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise _fail(f"{member.filename}: 图片损坏或格式不支持") from exc


def _box(line: str) -> dict[str, Any]:
    values = [float(value) for value in line.split()]
    if len(values) == 5:
        cx, cy, width, height = values[1:]
    else:
        xs, ys = values[1::2], values[2::2]
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    return {"class_id": int(values[0]), "cx": cx, "cy": cy, "width": width, "height": height}


def _parse_label(
    source: zipfile.ZipFile, member: zipfile.ZipInfo
) -> tuple[list[str], Counter[int], list[str]]:
    if member.file_size > MAX_TEXT_BYTES:
        return [], Counter(), [f"{member.filename}: 文件过大"]
    text = source.read(member).decode("utf-8-sig", "replace")
    lines: list[str] = []
    counts: Counter[int] = Counter()
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        values = raw.split()
        if not values:
            continue
        problem = _check_line(values)
        if problem:
            problems.append(f"{member.filename} 第 {number} 行：{problem}")
            continue
        counts[int(float(values[0]))] += 1
        lines.append(" ".join(values))
    return lines, counts, problems


def _check_line(values: list[str]) -> str | None:
    # 检测框 5 列；多边形（分割标注）1 + 2n 列，训练检测模型时 Ultralytics 会换算成外接框
    if len(values) != 5 and (len(values) < 7 or len(values) % 2 == 0):
        return f"应为 5 列（类别 cx cy w h），实际 {len(values)} 列"
    try:
        numbers = [float(value) for value in values]
    except ValueError:
        return "包含非数字内容"
    if not all(math.isfinite(value) for value in numbers):
        return "类别编号和坐标必须为有限数值"
    if numbers[0] < 0 or numbers[0] != int(numbers[0]):
        return "类别编号必须是非负整数"
    if any(value < 0 or value > 1.0001 for value in numbers[1:]):
        return "坐标须为 0~1 之间的归一化数值"
    if len(values) == 5 and (numbers[3] <= 0 or numbers[4] <= 0):
        return "框的宽高必须大于 0"
    return None


def _unique_stem(path: PurePosixPath, used: set[str]) -> str:
    stem = path.stem
    if stem in used:
        # 不同子目录里的同名图片，用上级目录名区分
        prefix = "_".join(part for part in path.parts[:-1]
                          if not _split_of(part) and part.lower() != "images") or "image"
        candidate = f"{prefix}_{stem}"
        counter = 2
        while candidate in used:
            candidate = f"{prefix}_{stem}_{counter}"
            counter += 1
        stem = candidate
    used.add(stem)
    return stem
