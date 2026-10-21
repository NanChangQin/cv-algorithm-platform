from threading import RLock
from uuid import UUID

from ..domain.entities import AlgorithmComparison


class InMemoryComparisonRepository:
    def __init__(self) -> None:
        self._items: dict[UUID, AlgorithmComparison] = {}
        self._lock = RLock()

    def add(self, comparison: AlgorithmComparison) -> None:
        with self._lock:
            self._items[comparison.id] = comparison

    def get(self, comparison_id: UUID) -> AlgorithmComparison | None:
        with self._lock:
            return self._items.get(comparison_id)

    def list(self) -> list[AlgorithmComparison]:
        with self._lock:
            return sorted(self._items.values(), key=lambda item: item.created_at, reverse=True)

    def list_recent(self, project_id: UUID, limit: int) -> list[AlgorithmComparison]:
        return [item for item in self.list() if item.project_id == project_id][:limit]
