"""部署所需的文件在节点之间传输：算法镜像、算法包和模型权重。

主服务器负责中转：从有镜像/权重的节点下载，再上传到目标节点。这里只提供单个节点上的
导出、导入和清单接口，所有路径和镜像名都经过校验，不能越出受控目录或导入任意镜像。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import tarfile
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .application.errors import ManagerError

SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_PACKAGE_BYTES = 4 * 1024**3


def safe_relative(path: str, *, max_parts: int = 8) -> PurePosixPath:
    parts = PurePosixPath(path).parts
    if not parts or len(parts) > max_parts or any(not SEGMENT.fullmatch(part) for part in parts):
        raise ManagerError("INPUT_INVALID", "路径不合法")
    return PurePosixPath(*parts)


class TransferService:
    def __init__(
        self,
        client: Any,
        data_root: Path,
        package_root: Path,
        model_root: Path,
        validate_image: Any,
    ) -> None:
        self._client = client
        self._staging = data_root / "transfers"
        self._package_root = package_root / "remote"
        self._model_root = model_root
        self._validate_image = validate_image
        self._hash_cache: dict[Path, tuple[int, int, str]] = {}

    def staging_file(self, suffix: str, *, for_models: bool = False) -> Path:
        # 权重上传完成后要原子地移进模型目录，所以和模型目录放在同一个文件系统里
        directory = self._model_root / ".incoming" if for_models else self._staging
        directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=directory, suffix=suffix, delete=False) as target:
            return Path(target.name)

    # 镜像 -----------------------------------------------------------------

    def export_image(self, image: str) -> Iterator[bytes]:
        self._check_image(image)
        try:
            found = self._client.images.get(image)
        except Exception as exc:
            raise ManagerError("IMAGE_MISSING", f"节点上没有镜像 {image}", 404) from exc
        return found.save(chunk_size=4 * 1024 * 1024, named=True)

    def import_image(self, image: str, archive: Path) -> dict[str, object]:
        self._check_image(image)
        tags = _archive_tags(archive)
        if tags != [image]:
            raise ManagerError("IMAGE_INVALID", "镜像文件里的标签与请求的镜像不一致")
        try:
            with archive.open("rb") as stream:
                self._client.images.load(stream)
            loaded = self._client.images.get(image)
        except Exception as exc:
            raise ManagerError("IMAGE_IMPORT_FAILED", f"镜像导入失败：{exc}", 502) from exc
        return {"image": image, "exists": True, "id": loaded.id}

    def _check_image(self, image: str) -> None:
        try:
            self._validate_image(image)
        except ValueError as exc:
            raise ManagerError("INPUT_INVALID", str(exc)) from exc

    # 算法包 ---------------------------------------------------------------

    def store_package(self, name: str, archive: Path) -> dict[str, object]:
        safe_relative(name, max_parts=1)
        target = self._package_root / name
        staging = self._package_root / f".{name}.staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        try:
            with zipfile.ZipFile(archive) as bundle:
                total = 0
                for member in bundle.infolist():
                    path = PurePosixPath(member.filename)
                    file_type = (member.external_attr >> 16) & 0o170000
                    if path.is_absolute() or ".." in path.parts:
                        raise ManagerError("PACKAGE_INVALID", "算法包包含非法路径")
                    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                        raise ManagerError("PACKAGE_INVALID", "算法包只能包含普通文件")
                    total += member.file_size
                    if total > MAX_PACKAGE_BYTES:
                        raise ManagerError("PACKAGE_INVALID", "算法包解压后体积过大")
                    output = staging / path
                    if member.is_dir():
                        output.mkdir(parents=True, exist_ok=True)
                        continue
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, output.open("wb") as writer:
                        shutil.copyfileobj(source, writer, 1024 * 1024)
            shutil.rmtree(target, ignore_errors=True)
            staging.rename(target)
        except zipfile.BadZipFile as exc:
            raise ManagerError("PACKAGE_INVALID", "算法包不是有效的 ZIP 文件") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return {"package_path": str(target)}

    # 模型权重 -------------------------------------------------------------

    def list_models(self, directory: str) -> dict[str, dict[str, object]]:
        root = self._model_directory(directory)
        if not root.is_dir():
            return {}
        files = {}
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                files[path.relative_to(root).as_posix()] = {
                    "size": path.stat().st_size,
                    "sha256": self._sha256(path),
                }
        return files

    def model_file(self, directory: str, name: str) -> Path:
        path = self._model_directory(directory) / safe_relative(name)
        if not path.is_file() or path.is_symlink():
            raise ManagerError("MODEL_FILE_MISSING", "权重文件不存在", 404)
        return path

    def store_model_file(
        self, directory: str, name: str, upload: Path, sha256: str
    ) -> dict[str, object]:
        target = self._model_directory(directory) / safe_relative(name)
        if _sha256_of(upload) != sha256:
            raise ManagerError("MODEL_FILE_INVALID", "权重传输不完整，校验值不一致")
        target.parent.mkdir(parents=True, exist_ok=True)
        # 算法容器以 uid 10001 只读挂载权重目录
        for parent in [*reversed(target.relative_to(self._model_root).parents)][1:]:
            (self._model_root / parent).chmod(0o755)
        upload.chmod(0o644)
        upload.replace(target)
        return {"size": target.stat().st_size, "sha256": sha256}

    def _model_directory(self, directory: str) -> Path:
        return self._model_root / safe_relative(directory, max_parts=3)

    def _sha256(self, path: Path) -> str:
        info = path.stat()
        cached = self._hash_cache.get(path)
        if cached and cached[:2] == (info.st_size, info.st_mtime_ns):
            return cached[2]
        digest = _sha256_of(path)
        self._hash_cache[path] = (info.st_size, info.st_mtime_ns, digest)
        return digest


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while chunk := reader.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_tags(archive: Path) -> list[str]:
    """docker save 生成的 tar 里 manifest.json 记录了要导入的镜像标签。"""
    try:
        with tarfile.open(archive) as bundle:
            member = bundle.getmember("manifest.json")
            if member.size > 1024 * 1024:
                raise ManagerError("IMAGE_INVALID", "镜像清单过大")
            reader = bundle.extractfile(member)
            manifest = json.loads(reader.read()) if reader else None
    except (tarfile.TarError, KeyError, ValueError) as exc:
        raise ManagerError("IMAGE_INVALID", "不是有效的镜像文件") from exc
    if not isinstance(manifest, list):
        raise ManagerError("IMAGE_INVALID", "不是有效的镜像文件")
    return [tag for item in manifest for tag in (item.get("RepoTags") or [])]


async def _receive(request: Request, target: Path, limit: int) -> str:
    digest = hashlib.sha256()
    size = 0
    try:
        with target.open("wb") as writer:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise ManagerError("UPLOAD_TOO_LARGE", "上传内容超过节点限制", 413)
                digest.update(chunk)
                writer.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def build_transfer_router(get_transfer: Any, max_image_bytes: int = 64 * 1024**3) -> APIRouter:
    router = APIRouter(tags=["transfer"])

    @router.get("/images/export")
    def export_image(image: str, service: TransferService = Depends(get_transfer)):
        return StreamingResponse(service.export_image(image), media_type="application/x-tar")

    @router.put("/images/import")
    async def import_image(
        image: str, request: Request, service: TransferService = Depends(get_transfer)
    ):
        archive = service.staging_file(".tar")
        try:
            await _receive(request, archive, max_image_bytes)
            return await run_in_threadpool(service.import_image, image, archive)
        finally:
            archive.unlink(missing_ok=True)

    @router.put("/packages/{name}")
    async def upload_package(
        name: str, request: Request, service: TransferService = Depends(get_transfer)
    ):
        archive = service.staging_file(".zip")
        try:
            await _receive(request, archive, MAX_PACKAGE_BYTES)
            return await run_in_threadpool(service.store_package, name, archive)
        finally:
            archive.unlink(missing_ok=True)

    @router.get("/models/{directory:path}/files/{name:path}")
    def download_model_file(
        directory: str, name: str, service: TransferService = Depends(get_transfer)
    ):
        return FileResponse(service.model_file(directory, name))

    @router.put("/models/{directory:path}/files/{name:path}")
    async def upload_model_file(
        directory: str,
        name: str,
        request: Request,
        service: TransferService = Depends(get_transfer),
    ):
        expected = request.headers.get("X-Content-SHA256", "").lower()
        if len(expected) != 64:
            raise ManagerError("INPUT_INVALID", "缺少校验值 X-Content-SHA256")
        upload = service.staging_file(".part", for_models=True)
        try:
            await _receive(request, upload, max_image_bytes)
            return await run_in_threadpool(
                service.store_model_file, directory, name, upload, expected
            )
        finally:
            upload.unlink(missing_ok=True)

    @router.get("/models/{directory:path}")
    def list_models(directory: str, service: TransferService = Depends(get_transfer)):
        return {"directory": directory, "files": service.list_models(directory)}

    return router
