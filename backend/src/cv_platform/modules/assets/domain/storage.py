from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol


@dataclass(frozen=True, slots=True)
class StoredImage:
    original_name: str
    storage_key: str
    algorithm_uri: str
    sha256: str
    media_type: str
    width: int
    height: int
    size_bytes: int


class AssetStorage(Protocol):
    def store_image(
        self,
        stream: BinaryIO,
        original_name: str,
        declared_media_type: str | None,
    ) -> StoredImage: ...

    def resolve(self, storage_key: str) -> Path: ...

    def thumbnail(self, storage_key: str, size: int) -> Path: ...
