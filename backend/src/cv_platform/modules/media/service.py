import ipaddress
from threading import RLock
from urllib.parse import urlparse
from uuid import UUID

from ...core.errors import ApplicationError
from ...core.project_context import DEFAULT_PROJECT_ID
from .domain import MediaSource, MediaSourceType


class InMemoryMediaSourceService:
    def __init__(self) -> None:
        self._items: dict[UUID, MediaSource] = {}
        self._lock = RLock()

    def create(
        self,
        name: str,
        source_type: MediaSourceType,
        uri: str,
        project_id: UUID = DEFAULT_PROJECT_ID,
        owner_id: str = "local-admin",
    ) -> MediaSource:
        self._validate_uri(source_type, uri)
        source = MediaSource.create(name.strip(), source_type, uri, project_id, owner_id)
        with self._lock:
            self._items[source.id] = source
        return source

    def list(self, project_id: UUID | None = None) -> list[MediaSource]:
        with self._lock:
            return sorted(
                [
                    item for item in self._items.values()
                    if project_id is None or item.project_id == project_id
                ],
                key=lambda item: item.created_at,
                reverse=True,
            )

    def get(self, source_id: UUID, project_id: UUID | None = None) -> MediaSource:
        with self._lock:
            source = self._items.get(source_id)
        if source is None or (project_id is not None and source.project_id != project_id):
            raise ApplicationError("MEDIA_SOURCE_NOT_FOUND", "媒体源不存在", 404)
        return source

    def delete(self, source_id: UUID, project_id: UUID | None = None) -> None:
        self.get(source_id, project_id)
        with self._lock:
            self._items.pop(source_id, None)

    @staticmethod
    def _validate_uri(source_type: MediaSourceType, uri: str) -> None:
        parsed = urlparse(uri)
        if source_type is MediaSourceType.RTSP:
            if parsed.scheme not in {"rtsp", "rtsps"} or not parsed.hostname:
                raise ApplicationError("MEDIA_SOURCE_INVALID", "RTSP 地址格式不合法")
            try:
                address = ipaddress.ip_address(parsed.hostname)
                if address.is_unspecified or address.is_multicast:
                    raise ApplicationError("MEDIA_SOURCE_INVALID", "RTSP 地址不可访问")
            except ValueError:
                pass
        elif source_type is MediaSourceType.CAMERA:
            if parsed.scheme != "camera" or not parsed.path.isdigit():
                raise ApplicationError("MEDIA_SOURCE_INVALID", "摄像头 URI 应为 camera:N")
        elif source_type is MediaSourceType.VIDEO:
            if parsed.scheme != "file" or not parsed.path.startswith("/data/"):
                raise ApplicationError("MEDIA_SOURCE_INVALID", "视频必须位于受控 /data 目录")
