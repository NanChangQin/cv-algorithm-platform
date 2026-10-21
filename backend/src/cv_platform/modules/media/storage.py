import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from ...core.errors import ApplicationError

VIDEO_TYPES = {
    ".mp4": {"video/mp4"},
    ".avi": {"video/x-msvideo", "video/avi"},
    ".mov": {"video/quicktime"},
    ".mkv": {"video/x-matroska", "application/octet-stream"},
    ".webm": {"video/webm"},
}


class LocalVideoStorage:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self._root = (root.resolve() / "videos")
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes

    def store(
        self,
        stream: BinaryIO,
        original_name: str,
        media_type: str | None,
    ) -> tuple[str, str, int]:
        safe_name = Path(original_name).name.strip()
        if not safe_name or safe_name != original_name:
            raise ApplicationError("VIDEO_INVALID", "视频文件名不合法")
        extension = Path(safe_name).suffix.lower()
        if extension not in VIDEO_TYPES:
            raise ApplicationError("VIDEO_INVALID", "仅支持 MP4、AVI、MOV、MKV 和 WebM")
        if media_type and media_type not in VIDEO_TYPES[extension]:
            raise ApplicationError("VIDEO_INVALID", "视频 MIME 与扩展名不匹配")
        digest = hashlib.sha256()
        size = 0
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(dir=self._root, delete=False, suffix=".upload") as target:
                temporary_path = Path(target.name)
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise ApplicationError(
                            "VIDEO_TOO_LARGE",
                            f"视频不能超过 {self._max_bytes // (1024 * 1024)} MB",
                            413,
                        )
                    digest.update(chunk)
                    target.write(chunk)
            if size == 0:
                raise ApplicationError("VIDEO_INVALID", "上传视频为空")
            sha256 = digest.hexdigest()
            final_path = self._root / sha256[:2] / f"{sha256}{extension}"
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if final_path.exists():
                temporary_path.unlink(missing_ok=True)
            else:
                os.replace(temporary_path, final_path)
            temporary_path = None
            relative = final_path.relative_to(self._root.parent).as_posix()
            return f"file:///data/{relative}", sha256, size
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
