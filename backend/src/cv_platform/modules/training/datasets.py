"""YOLO 目标检测数据集的导入、校验和规整。

接受常见的几种目录结构（Ultralytics / Roboflow 导出、只有 images 和 labels 的平铺目录），
统一整理成下面的结构再保存，训练节点和训练脚本只需处理这一种：

    data.yaml            names: [类别名, ...]
    images/train/*.jpg   labels/train/*.txt
    images/val/*.jpg     labels/val/*.txt
"""

from __future__ import annotations

import hashlib
import math
import re
import shutil
import stat
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO

import yaml

from ...core.errors import ApplicationError

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
SPLITS = {"train": "train", "training": "train", "val": "val", "valid": "val",
          "validation": "val", "test": "test"}
SPLIT_DIRECTORY = re.compile(r"(training|train|validation|valid|val|test)[-_]?\d{0,4}")
YAML_NAMES = {"data.yaml", "data.yml", "dataset.yaml", "dataset.yml"}
NAME_FILES = {"classes.txt", "obj.names", "classes.names"}
MAX_TEXT_BYTES = 1024 * 1024
# 没有划分验证集时，按路径散列取约十分之一作验证集
AUTO_VAL_RATIO = 10
MAX_EXAMPLES = 8


@dataclass(frozen=True, slots=True)
class ImportedDataset:
    class_names: tuple[str, ...]
    stats: dict[str, Any]
    size_bytes: int
    sha256: str


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

    def delete(self, dataset_id: object) -> None:
        self.path(dataset_id).unlink(missing_ok=True)

    def delete_run(self, run_id: object) -> None:
        shutil.rmtree(self.run_directory(run_id), ignore_errors=True)

    def import_archive(
        self, stream: BinaryIO, filename: str, dataset_id: object
    ) -> ImportedDataset:
        if not filename.lower().endswith(".zip"):
            raise _fail("请上传 ZIP 格式的数据集")
        target = self.path(dataset_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        upload = self._receive(stream, target.parent)
        try:
            with zipfile.ZipFile(upload) as source:
                return self._normalize(source, target)
        except zipfile.BadZipFile as exc:
            raise _fail("数据集不是有效的 ZIP 文件") from exc
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
                path.unlink(missing_ok=True)
                raise
        return path

    def _normalize(self, source: zipfile.ZipFile, target: Path) -> ImportedDataset:
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

        used_labels: set[PurePosixPath] = set()
        for image in images:
            for candidate in _label_candidates(image.path):
                if candidate in labels:
                    image.label = labels[candidate]
                    used_labels.add(candidate)
                    break

        warnings: list[str] = []
        class_names = self._class_names(source, yaml_files, name_files)
        splits, auto_split = _assign_splits(images, warnings)

        parsed: dict[int, tuple[list[str], Counter[int]]] = {}
        invalid_lines = 0
        invalid_examples: list[str] = []
        max_class = -1
        for index, image in enumerate(images):
            if image.label is None or splits[index] is None:
                continue
            lines, counts, problems = _parse_label(source, image.label)
            invalid_lines += len(problems)
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
            warnings.append(f"{invalid_lines} 行标注格式不正确，已删除")
        test_images = sum(split == "test" for split in splits)
        if test_images:
            warnings.append(f"测试集 {test_images} 张图片不参与训练")

        stats = self._write(source, target, images, splits, parsed, class_names)
        stats.update({
            "auto_split": auto_split,
            "warnings": warnings,
            "invalid_lines": invalid_lines,
            "invalid_examples": invalid_examples,
            "orphan_labels": orphan_labels,
            "skipped_files": skipped,
        })
        digest = hashlib.sha256()
        with target.open("rb") as written:
            while chunk := written.read(1024 * 1024):
                digest.update(chunk)
        return ImportedDataset(class_names, stats, target.stat().st_size, digest.hexdigest())

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
        return members

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
    ) -> dict[str, Any]:
        summary = {
            split: {"images": 0, "labeled": 0, "backgrounds": 0, "boxes": 0}
            for split in ("train", "val")
        }
        instances: Counter[int] = Counter()
        used_names: dict[str, set[str]] = {"train": set(), "val": set()}
        staging = target.with_name(f".{target.name}.staging")
        try:
            with zipfile.ZipFile(staging, "w") as output:
                output.writestr(
                    "data.yaml",
                    yaml.safe_dump({"names": list(class_names), "nc": len(class_names)},
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
                        zipfile.ZipInfo(f"images/{split}/{name}"), "w"
                    ) as writer:
                        shutil.copyfileobj(reader, writer, 1024 * 1024)
                    counts = summary[split]
                    counts["images"] += 1
                    lines, boxes = parsed.get(index, ([], Counter()))
                    if lines:
                        counts["labeled"] += 1
                        counts["boxes"] += sum(boxes.values())
                        instances.update(boxes)
                        output.writestr(
                            f"labels/{split}/{stem}.txt", "\n".join(lines) + "\n",
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
        }


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


def _assign_splits(images: list[_Image], warnings: list[str]) -> tuple[list[str | None], bool]:
    declared = {image.split for image in images}
    splits: list[str | None] = [image.split for image in images]
    if "val" not in declared and "test" in declared:
        splits = ["val" if split == "test" else split for split in splits]
        warnings.append("没有验证集，使用测试集作为验证集")
        return splits, False
    if "val" in declared:
        if "train" not in declared:
            if None not in declared:
                raise _fail("只有验证集，没有训练集")
            splits = ["train" if split is None else split for split in splits]
        if unassigned := splits.count(None):
            warnings.append(f"{unassigned} 张图片不在 train / val 目录中，已忽略")
        return splits, False
    # 没有验证集：从训练图片（或未划分的图片）中按路径散列抽取约 10%
    pool = [index for index, split in enumerate(splits) if split in {None, "train"}]
    if len(pool) < 2:
        raise _fail("至少需要 2 张图片才能划分训练集和验证集")
    chosen = [
        index for index in pool
        if int(hashlib.sha1(str(images[index].path).encode()).hexdigest(), 16)
        % AUTO_VAL_RATIO == 0
    ] or [pool[-1]]
    if len(chosen) == len(pool):
        chosen = chosen[:-1]
    validation = set(chosen)
    for index in pool:
        splits[index] = "val" if index in validation else "train"
    warnings.append(f"没有验证集，已自动抽取 {len(chosen)} 张图片作为验证集")
    return splits, True


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
