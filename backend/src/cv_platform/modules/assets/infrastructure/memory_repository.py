from __future__ import annotations

from threading import RLock
from uuid import UUID

from ..domain.entities import ImageAsset
from ..domain.repositories import AssetQuery


def _matches(asset: ImageAsset, criteria: AssetQuery) -> bool:
    return (
        asset.project_id == criteria.project_id
        and (not criteria.text or criteria.text.lower() in asset.original_name.lower())
        and (not criteria.ids or asset.id in criteria.ids)
    )


class InMemoryAssetRepository:
    def __init__(self) -> None:
        self._items: dict[UUID, ImageAsset] = {}
        self._lock = RLock()

    def add(self, asset: ImageAsset) -> None:
        with self._lock:
            self._items[asset.id] = asset

    def list(self) -> list[ImageAsset]:
        with self._lock:
            return sorted(self._items.values(), key=lambda item: item.created_at, reverse=True)

    def get(self, asset_id: UUID) -> ImageAsset | None:
        with self._lock:
            return self._items.get(asset_id)

    def _matching(self, criteria: AssetQuery) -> list[ImageAsset]:
        with self._lock:
            items = [asset for asset in self._items.values() if _matches(asset, criteria)]
        if criteria.order == "name":
            return sorted(items, key=lambda item: (item.original_name, item.id))
        return sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)

    def query(self, criteria: AssetQuery) -> list[ImageAsset]:
        items = self._matching(criteria)[criteria.offset:]
        return items if criteria.limit is None else items[: criteria.limit]

    def count(self, criteria: AssetQuery) -> int:
        return len(self._matching(criteria))

    def find_by_sha256(self, project_id: UUID, sha256: str) -> ImageAsset | None:
        return next(
            (
                item for item in self._matching(AssetQuery(project_id=project_id))
                if item.sha256 == sha256
            ),
            None,
        )

    def find_by_algorithm_uri(self, project_id: UUID, algorithm_uri: str) -> ImageAsset | None:
        return next(
            (
                item for item in self._matching(AssetQuery(project_id=project_id))
                if item.algorithm_uri == algorithm_uri
            ),
            None,
        )
