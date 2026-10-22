from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ....core.project_context import DEFAULT_PROJECT_ID


@dataclass(frozen=True, slots=True)
class ImageAsset:
    id: UUID
    original_name: str
    storage_key: str
    algorithm_uri: str
    sha256: str
    media_type: str
    width: int
    height: int
    size_bytes: int
    created_at: datetime
    owner_id: str = "local-admin"
    project_id: UUID = DEFAULT_PROJECT_ID

    @classmethod
    def create(
        cls,
        *,
        original_name: str,
        storage_key: str,
        algorithm_uri: str,
        sha256: str,
        media_type: str,
        width: int,
        height: int,
        size_bytes: int,
        project_id: UUID = DEFAULT_PROJECT_ID,
        owner_id: str = "local-admin",
    ) -> "ImageAsset":
        return cls(
            id=uuid4(),
            original_name=original_name,
            storage_key=storage_key,
            algorithm_uri=algorithm_uri,
            sha256=sha256,
            media_type=media_type,
            width=width,
            height=height,
            size_bytes=size_bytes,
            created_at=datetime.now(UTC),
            project_id=project_id,
            owner_id=owner_id,
        )
