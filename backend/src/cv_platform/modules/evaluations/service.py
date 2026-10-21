from __future__ import annotations

from typing import Any
from uuid import UUID

from cv_algorithm_sdk import TaskStatus

from ...core.errors import ApplicationError
from ..algorithms.domain.repositories import AlgorithmRepository
from ..assets.domain.repositories import AssetQuery, AssetRepository
from ..tasks.application.service import TaskService
from ..tasks.domain.repositories import TaskQuery, TaskRepository
from .annotations import parse_annotations
from .domain import EvaluationDataset, EvaluationRun
from .metrics import PRIMARY_METRIC, evaluate_result, summarize
from .repository import InMemoryEvaluationRepository, PostgresEvaluationRepository

# 图片数 × 算法数，一次评测最多创建这么多推理任务
MAX_RUN_TASKS = 2000


class EvaluationService:
    def __init__(
        self,
        repository: InMemoryEvaluationRepository | PostgresEvaluationRepository,
        tasks: TaskService,
        task_repository: TaskRepository,
        assets: AssetRepository,
        algorithms: AlgorithmRepository,
        project_id: UUID,
        actor: str,
    ) -> None:
        self._repository = repository
        self._tasks = tasks
        self._task_repository = task_repository
        self._assets = assets
        self._algorithms = algorithms
        self._project_id = project_id
        self._actor = actor

    def list_datasets(self) -> list[EvaluationDataset]:
        return self._repository.list_datasets(self._project_id)

    def get_dataset(self, dataset_id: UUID) -> EvaluationDataset:
        dataset = self._repository.get_dataset(dataset_id)
        if dataset is None or dataset.project_id != self._project_id:
            raise ApplicationError("DATASET_NOT_FOUND", "评测集不存在", 404)
        return dataset

    def create_dataset(
        self,
        name: str,
        task_type: str,
        annotations_filename: str,
        annotations: str,
        asset_ids: list[UUID],
    ) -> tuple[EvaluationDataset, list[str], list[str]]:
        assets = self._assets.query(AssetQuery(self._project_id, ids=tuple(asset_ids)))
        if not assets:
            raise ApplicationError("DATASET_EMPTY", "没有找到上传的图片", 400)
        items, unmatched, unlabeled = parse_annotations(
            task_type, annotations_filename, annotations, assets
        )
        if not items:
            raise ApplicationError(
                "DATASET_EMPTY", "标注里的文件名和上传的图片都对不上，请检查文件名", 400
            )
        dataset = EvaluationDataset.create(
            self._project_id, name.strip(), task_type, tuple(items), self._actor
        )
        self._repository.add_dataset(dataset)
        return dataset, unmatched, unlabeled

    def delete_dataset(self, dataset_id: UUID) -> None:
        self.get_dataset(dataset_id)
        self._repository.delete_dataset(dataset_id)

    def list_runs(self) -> list[EvaluationRun]:
        return self._repository.list_runs(self._project_id)

    def is_finished(self, run: EvaluationRun) -> bool:
        """报告还没生成时，看这次评测的任务里还有没有没结束的。"""
        if run.report is not None:
            return True
        ids = tuple(UUID(task_id) for task_id in run.task_ids.values())
        active = (TaskStatus.QUEUED, TaskStatus.PREPARING, TaskStatus.STARTING, TaskStatus.RUNNING)
        return not self._task_repository.count(
            TaskQuery(project_id=self._project_id, ids=ids, statuses=active)
        )

    def get_run(self, run_id: UUID) -> EvaluationRun:
        run = self._repository.get_run(run_id)
        if run is None or run.project_id != self._project_id:
            raise ApplicationError("EVALUATION_NOT_FOUND", "评测记录不存在", 404)
        return run

    def start_run(
        self,
        dataset_id: UUID,
        algorithm_version_ids: list[UUID],
        parameters: dict[str, dict[str, Any]],
    ) -> EvaluationRun:
        dataset = self.get_dataset(dataset_id)
        unique = list(dict.fromkeys(algorithm_version_ids))
        if not unique:
            raise ApplicationError("EVALUATION_INVALID", "至少选择一个算法", 400)
        if len(unique) * len(dataset.items) > MAX_RUN_TASKS:
            raise ApplicationError(
                "EVALUATION_TOO_LARGE",
                f"一次评测最多 {MAX_RUN_TASKS} 个推理任务（图片数 × 算法数），"
                "请减少算法或拆分评测集",
                400,
            )
        asset_ids = tuple(item.asset_id for item in dataset.items)
        present = self._assets.count(AssetQuery(self._project_id, ids=asset_ids))
        if present != len(set(asset_ids)):
            raise ApplicationError(
                "DATASET_INCOMPLETE", "评测集里有图片已被删除，请重新创建评测集", 409
            )
        # 全部校验通过再创建任务，避免后面的算法不合格时留下一半任务
        for algorithm_id in unique:
            algorithm = self._algorithms.get(algorithm_id)
            if (
                algorithm is not None
                and algorithm.project_id == self._project_id
                and algorithm.manifest.task_type.value != dataset.task_type
            ):
                raise ApplicationError(
                    "EVALUATION_INVALID", f"{algorithm.manifest.name} 的任务类型与评测集不一致", 400
                )
            self._tasks.validate_task(
                algorithm_id, parameters.get(str(algorithm_id), {}), asset_id=asset_ids[0]
            )
        task_ids: dict[str, str] = {}
        for algorithm_id in unique:
            for item in dataset.items:
                task = self._tasks.create_task(
                    algorithm_id, parameters.get(str(algorithm_id), {}), asset_id=item.asset_id
                )
                task_ids[f"{algorithm_id}:{item.asset_id}"] = str(task.id)
        run = EvaluationRun.create(
            self._project_id, dataset.id, tuple(unique), task_ids, self._actor
        )
        self._repository.add_run(run)
        return run

    def report(self, run: EvaluationRun) -> dict[str, Any]:
        """按任务结果和标注计算指标；全部任务结束后缓存，之后直接返回缓存。"""
        if run.report is not None:
            return run.report
        dataset = self._repository.get_dataset(run.dataset_id)
        task_type = dataset.task_type if dataset else ""
        truth = {item.asset_id: item.ground_truth for item in dataset.items} if dataset else {}
        ids = tuple(UUID(task_id) for task_id in run.task_ids.values())
        tasks = {
            task.id: task
            for task in self._task_repository.query(TaskQuery(project_id=self._project_id, ids=ids))
        }
        metric = PRIMARY_METRIC.get(task_type, "")
        finished = True
        algorithms = []
        for algorithm_id in run.algorithm_version_ids:
            samples: list[dict[str, float]] = []
            worst: list[dict[str, Any]] = []
            counts = {"completed": 0, "failed": 0, "active": 0}
            for key, task_id in run.task_ids.items():
                prefix, _, asset_id = key.partition(":")
                if prefix != str(algorithm_id):
                    continue
                task = tasks.get(UUID(task_id))
                if task is not None and task.status not in {
                    TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED,
                }:
                    counts["active"] += 1
                    continue
                if task is None or task.status is not TaskStatus.COMPLETED or task.result is None:
                    counts["failed"] += 1
                    continue
                try:
                    sample = evaluate_result(task.result, truth.get(UUID(asset_id), {}))
                except (ValueError, KeyError, TypeError):
                    counts["failed"] += 1
                    continue
                counts["completed"] += 1
                samples.append(sample)
                worst.append(
                    {"task_id": task_id, "asset_id": asset_id, "value": sample.get(metric, 0.0)}
                )
            if counts["active"]:
                finished = False
            worst.sort(key=lambda item: item["value"])
            algorithms.append({
                "algorithm_version_id": str(algorithm_id),
                "total": sum(counts.values()),
                **counts,
                "metrics": summarize(task_type, samples),
                "worst": worst[:12],
            })
        report = {
            "task_type": task_type,
            "primary_metric": metric,
            "finished": finished,
            "algorithms": algorithms,
        }
        if finished:
            self._repository.save_report(run.id, report)
        return report
