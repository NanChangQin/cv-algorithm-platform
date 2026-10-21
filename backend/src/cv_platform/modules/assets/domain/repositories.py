from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .entities import ImageAsset


@dataclass(frozen=True, slots=True)
class AssetQuery:
    project_id: UUID
    # 按文件名模糊匹配，不区分大小写
    text: str | None = None
    ids: tuple[UUID, ...] = ()
    limit: int | None = None
    offset: int = 0


class AssetRepository(Protocol):
    def add(self, asset: ImageAsset) -> None: ...

    def list(self) -> list[ImageAsset]: ...

    def get(self, asset_id: UUID) -> ImageAsset | None: ...

    def query(self, criteria: AssetQuery) -> list[ImageAsset]:
        """按上传时间倒序返回符合条件的图片。"""
        ...

    def count(self, criteria: AssetQuery) -> int:
        """符合条件的图片总数，忽略 limit 和 offset。"""
        ...

    def find_by_sha256(self, project_id: UUID, sha256: str) -> ImageAsset | None: ...

    def find_by_algorithm_uri(self, project_id: UUID, algorithm_uri: str) -> ImageAsset | None: ...
