from typing import BinaryIO
from pathlib import Path
from uuid import UUID

from ....core.errors import ApplicationError
from ..domain.entities import ImageAsset
from ..domain.repositories import AssetQuery, AssetRepository
from ..domain.storage import AssetStorage
from ....core.project_context import DEFAULT_PROJECT_ID


class AssetService:
    def __init__(
        self,
        repository: AssetRepository,
        storage: AssetStorage,
        project_id: UUID = DEFAULT_PROJECT_ID,
        actor: str = "local-admin",
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._project_id = project_id
        self._actor = actor

    def upload(
        self,
        stream: BinaryIO,
        original_name: str,
        declared_media_type: str | None,
    ) -> ImageAsset:
        stored = self._storage.store_image(stream, original_name, declared_media_type)
        existing = self._repository.find_by_sha256(self._project_id, stored.sha256)
        if existing is not None:
            return existing
        asset = ImageAsset.create(
            original_name=stored.original_name,
            storage_key=stored.storage_key,
            algorithm_uri=stored.algorithm_uri,
            sha256=stored.sha256,
            media_type=stored.media_type,
            width=stored.width,
            height=stored.height,
            size_bytes=stored.size_bytes,
            project_id=self._project_id,
            owner_id=self._actor,
        )
        self._repository.add(asset)
        return asset

    def list_assets(
        self,
        *,
        text: str | None = None,
        ids: tuple[UUID, ...] = (),
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[ImageAsset], int]:
        """返回当前页的图片和符合条件的总数。"""
        criteria = AssetQuery(self._project_id, text=text, ids=ids, limit=limit, offset=offset)
        items = self._repository.query(criteria)
        total = len(items) if limit is None and not offset else self._repository.count(criteria)
        return items, total

    def register_local_image(
        self,
        storage_key: str,
        original_name: str,
        media_type: str = "image/jpeg",
        *,
        remove_source: bool = False,
    ) -> ImageAsset:
        path = self._storage.resolve(storage_key)
        with path.open("rb") as stream:
            asset = self.upload(stream, Path(original_name).name, media_type)
        if remove_source:
            # 内容已复制进按哈希寻址的资源目录，原文件（例如视频抽帧）不再需要
            path.unlink(missing_ok=True)
            for directory in (path.parent, path.parent.parent):
                try:
                    directory.rmdir()
                except OSError:
                    break
        return asset

    def get_asset(self, asset_id: UUID) -> ImageAsset:
        asset = self._repository.get(asset_id)
        if asset is None or asset.project_id != self._project_id:
            raise ApplicationError("ASSET_NOT_FOUND", "图片资源不存在", 404)
        return asset
