from typing import Any
from uuid import UUID

from ....core.errors import ApplicationError
from ....core.project_context import DEFAULT_PROJECT_ID
from ...tasks.application.service import TaskService
from ..domain.entities import AlgorithmComparison
from ..infrastructure.memory_repository import InMemoryComparisonRepository
from ..infrastructure.postgres_repository import PostgresComparisonRepository


class ComparisonService:
    def __init__(
        self,
        repository: InMemoryComparisonRepository | PostgresComparisonRepository,
        tasks: TaskService,
        project_id: UUID = DEFAULT_PROJECT_ID,
        actor: str = "local-admin",
    ) -> None:
        self._repository = repository
        self._tasks = tasks
        self._project_id = project_id
        self._actor = actor

    def create(
        self,
        asset_id: UUID,
        algorithm_version_ids: list[UUID],
        parameters: dict[str, dict[str, Any]],
    ) -> AlgorithmComparison:
        unique_ids = list(dict.fromkeys(algorithm_version_ids))
        if len(unique_ids) < 2:
            raise ApplicationError("COMPARISON_INVALID", "至少选择两个不同算法", 400)
        # 全部校验通过再创建，避免后面的算法参数不合法时前面的任务成为孤立任务
        for algorithm_id in unique_ids:
            self._tasks.validate_task(
                algorithm_id,
                parameters.get(str(algorithm_id), {}),
                asset_id=asset_id,
            )
        tasks = [
            self._tasks.create_task(
                algorithm_id,
                parameters.get(str(algorithm_id), {}),
                asset_id=asset_id,
            )
            for algorithm_id in unique_ids
        ]
        comparison = AlgorithmComparison.create(
            asset_id,
            [task.id for task in tasks],
            self._project_id,
            self._actor,
        )
        self._repository.add(comparison)
        return comparison

    def get(self, comparison_id: UUID) -> AlgorithmComparison:
        comparison = self._repository.get(comparison_id)
        if comparison is None or comparison.project_id != self._project_id:
            raise ApplicationError("COMPARISON_NOT_FOUND", "对比任务不存在", 404)
        return comparison

    def list_recent(self, limit: int) -> list[AlgorithmComparison]:
        return self._repository.list_recent(self._project_id, limit)
