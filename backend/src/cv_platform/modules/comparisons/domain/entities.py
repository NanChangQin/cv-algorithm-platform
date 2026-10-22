from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ....core.project_context import DEFAULT_PROJECT_ID


@dataclass(frozen=True, slots=True)
class AlgorithmComparison:
    id: UUID
    asset_id: UUID
    task_ids: tuple[UUID, ...]
    created_at: datetime
    owner_id: str = "local-admin"
    project_id: UUID = DEFAULT_PROJECT_ID

    @classmethod
    def create(
        cls,
        asset_id: UUID,
        task_ids: list[UUID],
        project_id: UUID = DEFAULT_PROJECT_ID,
        owner_id: str = "local-admin",
    ) -> "AlgorithmComparison":
        return cls(
            id=uuid4(),
            asset_id=asset_id,
            task_ids=tuple(task_ids),
            created_at=datetime.now(UTC),
            project_id=project_id,
            owner_id=owner_id,
        )
