import hashlib
import shutil
import stat
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import BinaryIO

import cv_algorithm_sdk
from cv_algorithm_sdk import AlgorithmManifest, load_manifest

from ....core.errors import ApplicationError


class AlgorithmPackageStorage:
    def __init__(
        self,
        root: Path,
        max_package_bytes: int,
        max_extracted_bytes: int,
        max_files: int,
    ) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_package_bytes = max_package_bytes
        self._max_extracted_bytes = max_extracted_bytes
        self._max_files = max_files
        self._extract_locks: defaultdict[str, Lock] = defaultdict(Lock)

    def import_package(
        self,
        stream: BinaryIO,
        filename: str,
    ) -> tuple[AlgorithmManifest, str, str]:
        if Path(filename).name != filename or not filename.lower().endswith(".zip"):
            raise ApplicationError("PACKAGE_INVALID", "算法包必须是合法 ZIP 文件")
        digest = hashlib.sha256()
        size = 0
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(dir=self._root, delete=False, suffix=".zip") as target:
                temporary_path = Path(target.name)
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > self._max_package_bytes:
                        raise ApplicationError("PACKAGE_TOO_LARGE", "算法包超过配置的大小限制", 413)
                    digest.update(chunk)
                    target.write(chunk)
            sha256 = digest.hexdigest()
            with self._extract_locks[sha256]:
                package_root = self._validate_and_extract(temporary_path, sha256)
                self._install_platform_sdk(package_root)
            manifest_path = package_root / "manifest.yaml"
            if not manifest_path.is_file():
                raise ApplicationError("PACKAGE_INVALID", "算法包根目录缺少 manifest.yaml")
            if not (package_root / "service.py").is_file():
                raise ApplicationError("PACKAGE_INVALID", "算法包根目录缺少 service.py")
            if not (package_root / "test" / "sample.jpg").is_file():
                raise ApplicationError(
                    "PACKAGE_INVALID",
                    "算法包缺少协议验收图片 test/sample.jpg",
                )
            try:
                manifest = load_manifest(manifest_path)
            except Exception as exc:
                raise ApplicationError("PACKAGE_INVALID", f"manifest 校验失败: {exc}") from exc
            final_zip = self._root / f"{manifest.id}-{manifest.version}-{sha256[:12]}.zip"
            temporary_path.replace(final_zip)
            temporary_path = None
            return manifest, str(package_root), sha256
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _validate_and_extract(self, archive_path: Path, sha256: str) -> Path:
        extract_root = self._root / "extracted" / sha256
        if extract_root.exists():
            shutil.rmtree(extract_root)
        extract_root.mkdir(parents=True)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                if len(members) > self._max_files:
                    raise ApplicationError("PACKAGE_INVALID", "算法包文件数量过多")
                total_size = sum(member.file_size for member in members)
                if total_size > self._max_extracted_bytes:
                    raise ApplicationError("PACKAGE_INVALID", "算法包解压后体积过大")
                if (size := archive_path.stat().st_size) and total_size / size > 200:
                    raise ApplicationError("PACKAGE_INVALID", "算法包压缩比异常")
                for member in members:
                    self._validate_member(member)
                    destination = (extract_root / PurePosixPath(member.filename)).resolve()
                    if extract_root not in destination.parents and destination != extract_root:
                        raise ApplicationError("PACKAGE_INVALID", "算法包包含路径穿越")
                    if member.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as source, destination.open("wb") as target:
                            shutil.copyfileobj(source, target)
            return extract_root
        except zipfile.BadZipFile as exc:
            raise ApplicationError("PACKAGE_INVALID", "算法包不是有效 ZIP 文件") from exc
        except Exception:
            shutil.rmtree(extract_root, ignore_errors=True)
            raise

    @staticmethod
    def _validate_member(member: zipfile.ZipInfo) -> None:
        path = PurePosixPath(member.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ApplicationError("PACKAGE_INVALID", "算法包包含非法路径")
        file_type = (member.external_attr >> 16) & 0o170000
        if file_type == stat.S_IFLNK:
            raise ApplicationError("PACKAGE_INVALID", "算法包不允许符号链接")
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise ApplicationError("PACKAGE_INVALID", "算法包包含特殊设备文件")
        if path.name.lower() == "dockerfile":
            raise ApplicationError("PACKAGE_INVALID", "不允许提交自定义 Dockerfile")

    @staticmethod
    def _install_platform_sdk(package_root: Path) -> None:
        sdk_source = Path(cv_algorithm_sdk.__file__).resolve().parent
        sdk_target = package_root / ".platform" / "cv_algorithm_sdk"
        sdk_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(sdk_source, sdk_target)
