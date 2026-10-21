import hashlib
import os
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import BinaryIO
from urllib.parse import quote

from PIL import Image, ImageOps, UnidentifiedImageError

from ....core.errors import ApplicationError
from ..domain.storage import StoredImage


FORMAT_INFO = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "BMP": (".bmp", "image/bmp"),
    "WEBP": (".webp", "image/webp"),
}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class LocalAssetStorage:
    def __init__(
        self,
        root: Path,
        algorithm_data_root: str,
        max_upload_bytes: int,
        max_image_pixels: int,
    ) -> None:
        self._root = root.resolve()
        self._assets_root = self._root / "assets"
        self._assets_root.mkdir(parents=True, exist_ok=True)
        self._algorithm_data_root = PurePosixPath(algorithm_data_root)
        self._max_upload_bytes = max_upload_bytes
        Image.MAX_IMAGE_PIXELS = max_image_pixels

    def store_image(
        self,
        stream: BinaryIO,
        original_name: str,
        declared_media_type: str | None,
    ) -> StoredImage:
        safe_name = self._validate_name(original_name)
        declared_extension = Path(safe_name).suffix.lower()
        if declared_extension not in ALLOWED_EXTENSIONS:
            raise ApplicationError("INPUT_INVALID", "仅支持 JPG、PNG、BMP 和 WebP 图片")

        digest = hashlib.sha256()
        size = 0
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                dir=self._assets_root,
                delete=False,
                suffix=".upload",
            ) as target:
                temporary_path = Path(target.name)
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > self._max_upload_bytes:
                        raise ApplicationError(
                            "INPUT_TOO_LARGE",
                            f"图片不能超过 {self._max_upload_bytes // (1024 * 1024)} MB",
                            413,
                        )
                    digest.update(chunk)
                    target.write(chunk)
            if size == 0:
                raise ApplicationError("INPUT_INVALID", "上传文件为空")

            image_format, width, height = self._inspect_image(temporary_path)
            extension, actual_media_type = FORMAT_INFO[image_format]
            if declared_media_type and declared_media_type != actual_media_type:
                raise ApplicationError("INPUT_INVALID", "文件 MIME 与实际图片格式不一致")
            if declared_extension not in {extension, ".jpeg" if extension == ".jpg" else extension}:
                raise ApplicationError("INPUT_INVALID", "文件扩展名与实际图片格式不一致")

            sha256 = digest.hexdigest()
            storage_key = f"assets/{sha256[:2]}/{sha256}{extension}"
            final_path = self.resolve(storage_key, must_exist=False)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if final_path.exists():
                temporary_path.unlink(missing_ok=True)
            else:
                os.replace(temporary_path, final_path)
            final_path.chmod(0o644)
            temporary_path = None
            algorithm_path = self._algorithm_data_root / PurePosixPath(storage_key)
            return StoredImage(
                original_name=safe_name,
                storage_key=storage_key,
                algorithm_uri=f"file://{quote(str(algorithm_path))}",
                sha256=sha256,
                media_type=actual_media_type,
                width=width,
                height=height,
                size_bytes=size,
            )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def thumbnail(self, storage_key: str, size: int) -> Path:
        """返回不超过 size×size 的 JPEG 缩略图；按内容哈希缓存，生成一次后直接复用。"""
        source = self.resolve(storage_key)
        target = self._root / "thumbnails" / str(size) / f"{source.stem}.jpg"
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            preview = ImageOps.exif_transpose(image)
            preview.thumbnail((size, size), Image.Resampling.LANCZOS)
            if preview.mode in {"RGBA", "LA", "P"}:
                preview = preview.convert("RGBA")
                background = Image.new("RGB", preview.size, "white")
                background.paste(preview, mask=preview.getchannel("A"))
                preview = background
            elif preview.mode != "RGB":
                preview = preview.convert("RGB")
            with NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
            try:
                preview.save(temporary, format="JPEG", quality=82, optimize=True)
                temporary.chmod(0o644)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return target

    def resolve(self, storage_key: str, *, must_exist: bool = True) -> Path:
        candidate = (self._root / storage_key).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ApplicationError("ASSET_PATH_INVALID", "资源路径不合法", 400)
        if must_exist and not candidate.is_file():
            raise ApplicationError("ASSET_FILE_MISSING", "资源文件不存在", 404)
        return candidate

    @staticmethod
    def _validate_name(original_name: str) -> str:
        name = Path(original_name).name.strip()
        if not name or name != original_name or any(ord(character) < 32 for character in name):
            raise ApplicationError("INPUT_INVALID", "文件名不合法")
        return name[:255]

    @staticmethod
    def _inspect_image(path: Path) -> tuple[str, int, int]:
        try:
            with Image.open(path) as image:
                image_format = image.format
                width, height = image.size
                image.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ApplicationError("INPUT_INVALID", "文件不是有效或安全的图片") from exc
        if image_format not in FORMAT_INFO or width <= 0 or height <= 0:
            raise ApplicationError("INPUT_INVALID", "不支持的图片格式")
        return image_format, width, height
