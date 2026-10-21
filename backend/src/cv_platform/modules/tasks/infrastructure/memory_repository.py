from __future__ import annotations

from datetime import datetime
from threading import RLock
from uuid import UUID

from cv_algorithm_sdk import TaskStatus

from ..domain.entities import InferenceTask
from ..domain.repositories import TaskQuery


def _matches(task: InferenceTask, criteria: TaskQuery) -> bool:
    node = task.execution_node_id or task.node_id
    search = criteria.search
    if search is not None:
        text = search.text.lower()
        found = (
            text in str(task.id)
            or task.algorithm_version_id in search.algorithm_ids
            or task.asset_id in search.asset_ids
            or node in search.node_ids
            or (node is not None and text in node.lower())
        )
        if not found:
            return False
    return (
        (criteria.project_id is None or task.project_id == criteria.project_id)
        and (
            criteria.algorithm_version_id is None
            or task.algorithm_version_id == criteria.algorithm_version_id
        )
        and (criteria.asset_id is None or task.asset_id == criteria.asset_id)
        and (not criteria.statuses or task.status in criteria.statuses)
        and (criteria.node_id is None or node == criteria.node_id)
        and (criteria.created_after is None or task.created_at >= criteria.created_after)
        and (not criteria.ids or task.id in criteria.ids)
    )


class InMemoryTaskRepository:
    def __init__(self) -> None:
        self._items: dict[UUID, InferenceTask] = {}
        self._lock = RLock()

    def add(self, task: InferenceTask) -> None:
        with self._lock:
            self._items[task.id] = task

    def save(self, task: InferenceTask) -> None:
        with self._lock:
            if task.id not in self._items:
                raise KeyError(f"task does not exist: {task.id}")
            self._items[task.id] = task

    def list(self) -> list[InferenceTask]:
        with self._lock:
            return sorted(self._items.values(), key=lambda item: item.created_at, reverse=True)

    def get(self, task_id: UUID) -> InferenceTask | None:
        with self._lock:
            return self._items.get(task_id)

    def _matching(self, criteria: TaskQuery) -> list[InferenceTask]:
        with self._lock:
            items = [task for task in self._items.values() if _matches(task, criteria)]
        return sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)

    def query(self, criteria: TaskQuery) -> list[InferenceTask]:
        items = self._matching(criteria)[criteria.offset:]
        return items if criteria.limit is None else items[: criteria.limit]

    def count(self, criteria: TaskQuery) -> int:
        return len(self._matching(criteria))

    def count_by_status(self, project_id: UUID | None = None) -> dict[TaskStatus, int]:
        counts: dict[TaskStatus, int] = {}
        for task in self._matching(TaskQuery(project_id=project_id)):
            counts[task.status] = counts.get(task.status, 0) + 1
        return counts

    def count_by_algorithm(self, statuses: tuple[TaskStatus, ...]) -> dict[UUID, int]:
        counts: dict[UUID, int] = {}
        for task in self._matching(TaskQuery(statuses=statuses)):
            counts[task.algorithm_version_id] = counts.get(task.algorithm_version_id, 0) + 1
        return counts

    def last_created_by_algorithm(self, project_id: UUID) -> dict[UUID, datetime]:
        latest: dict[UUID, datetime] = {}
        for task in self._matching(TaskQuery(project_id=project_id)):
            latest.setdefault(task.algorithm_version_id, task.created_at)
        return latest

    def inference_time_stats(self, project_id: UUID | None = None) -> tuple[float, int]:
        timings = [
            task.result.timing.inference_ms
            for task in self._matching(TaskQuery(project_id=project_id))
            if task.result is not None
        ]
        return (sum(timings) / len(timings) if timings else 0.0), len(timings)
