from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from ...core.project_context import DEFAULT_PROJECT_ID


class MediaSourceType(StrEnum):
    VIDEO = "video"
    RTSP = "rtsp"
    CAMERA = "camera"


@dataclass(frozen=True, slots=True)
class MediaSource:
    id: UUID
    name: str
    source_type: MediaSourceType
    uri: str
    enabled: bool
    created_at: datetime
    owner_id: str = "local-admin"
    project_id: UUID = DEFAULT_PROJECT_ID

    @classmethod
    def create(
        cls,
        name: str,
        source_type: MediaSourceType,
        uri: str,
        project_id: UUID = DEFAULT_PROJECT_ID,
        owner_id: str = "local-admin",
    ) -> "MediaSource":
        return cls(
            uuid4(),
            name,
            source_type,
            uri,
            True,
            datetime.now(UTC),
            owner_id=owner_id,
            project_id=project_id,
        )
