import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import UUID

from cv_algorithm_sdk import TaskStatus

from ...core.database import Database
from ...core.errors import ApplicationError
from ..algorithms.domain.repositories import AlgorithmRepository
from ..instances.application.service import RuntimeInstanceService
from ..tasks.domain.entities import InferenceTask
from ..tasks.domain.repositories import TaskQuery, TaskRepository


def _percentile(sorted_values: list[float], ratio: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return round(sorted_values[0], 3)
    position = (len(sorted_values) - 1) * ratio
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(sorted_values[lower], 3)
    weight = position - lower
    return round(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight, 3)


def _summarize_tasks(
    items: list[InferenceTask], *, now: datetime, window_seconds: int = 60
) -> dict[str, object]:
    inference_ms: list[float] = []
    end_to_end_ms: list[float] = []
    queue_wait_ms: list[float] = []
    completed = 0
    failed = 0
    in_flight = 0
    recent_completed = 0
    cutoff = now - timedelta(seconds=max(1, window_seconds))

    for task in items:
        if task.status is TaskStatus.COMPLETED:
            completed += 1
            if task.result is not None:
                inference_ms.append(float(task.result.timing.inference_ms))
            if task.completed_at is not None:
                end_to_end_ms.append(
                    max(0.0, (task.completed_at - task.created_at).total_seconds() * 1000.0)
                )
                if task.completed_at >= cutoff:
                    recent_completed += 1
            if task.started_at is not None:
                queue_wait_ms.append(
                    max(0.0, (task.started_at - task.created_at).total_seconds() * 1000.0)
                )
        elif task.status is TaskStatus.FAILED:
            failed += 1
            if task.started_at is not None:
                queue_wait_ms.append(
                    max(0.0, (task.started_at - task.created_at).total_seconds() * 1000.0)
                )
        elif task.status in {
            TaskStatus.QUEUED,
            TaskStatus.PREPARING,
            TaskStatus.STARTING,
            TaskStatus.RUNNING,
        }:
            in_flight += 1

    inference_ms.sort()
    end_to_end_ms.sort()
    queue_wait_ms.sort()
    terminal_total = completed + failed
    error_rate = round(failed / terminal_total, 4) if terminal_total else 0.0
    qps = round(recent_completed / max(1, window_seconds), 4)
    return {
        "completed_samples": len(inference_ms),
        "failed_tasks": failed,
        "in_flight_tasks": in_flight,
        "error_rate": error_rate,
        "qps_1m": qps,
        "average_inference_ms": (
            round(sum(inference_ms) / len(inference_ms), 3) if inference_ms else 0.0
        ),
        "inference_p50_ms": _percentile(inference_ms, 0.50),
        "inference_p95_ms": _percentile(inference_ms, 0.95),
        "inference_p99_ms": _percentile(inference_ms, 0.99),
        "average_end_to_end_ms": (
            round(sum(end_to_end_ms) / len(end_to_end_ms), 3) if end_to_end_ms else 0.0
        ),
        "end_to_end_p50_ms": _percentile(end_to_end_ms, 0.50),
        "end_to_end_p95_ms": _percentile(end_to_end_ms, 0.95),
        "end_to_end_p99_ms": _percentile(end_to_end_ms, 0.99),
        "average_queue_wait_ms": (
            round(sum(queue_wait_ms) / len(queue_wait_ms), 3) if queue_wait_ms else 0.0
        ),
        "queue_wait_p50_ms": _percentile(queue_wait_ms, 0.50),
        "queue_wait_p95_ms": _percentile(queue_wait_ms, 0.95),
        "queue_wait_p99_ms": _percentile(queue_wait_ms, 0.99),
    }


@dataclass(frozen=True, slots=True)
class AutoscalingPolicy:
    algorithm_version_id: UUID
    min_replicas: int
    max_replicas: int
    target_concurrency: int
    idle_seconds: int
    updated_at: datetime


class OperationsService:
    def __init__(
        self,
        algorithms: AlgorithmRepository,
        tasks: TaskRepository,
        instances: RuntimeInstanceService,
        database: Database | None = None,
        deployments=None,
    ) -> None:
        self._algorithms = algorithms
        self._tasks = tasks
        self._instances = instances
        self._database = database
        self._deployments = deployments
        self._policies: dict[UUID, AutoscalingPolicy] = {}
        self._last_active_at: dict[UUID, datetime] = {}
        self._lock = RLock()

    def set_traffic(self, weights: dict[UUID, int], project_id: UUID) -> list:
        if not weights or sum(weights.values()) != 100:
            raise ApplicationError("TRAFFIC_INVALID", "同一算法各版本流量权重之和必须为 100")
        algorithms = [self._algorithms.get(version_id) for version_id in weights]
        if any(item is None or item.project_id != project_id for item in algorithms):
            raise ApplicationError("ALGORITHM_NOT_FOUND", "算法版本不存在", 404)
        keys = {item.manifest.id for item in algorithms if item is not None}
        if len(keys) != 1:
            raise ApplicationError("TRAFFIC_INVALID", "灰度权重只能配置同一算法的不同版本")
        updated = []
        for algorithm in algorithms:
            if algorithm is None:
                continue
            algorithm = algorithm.with_traffic_weight(weights[algorithm.id])
            self._algorithms.save(algorithm)
            updated.append(algorithm)
        return updated

    def set_policy(
        self,
        algorithm_version_id: UUID,
        min_replicas: int,
        max_replicas: int,
        target_concurrency: int,
        idle_seconds: int,
        project_id: UUID,
    ) -> AutoscalingPolicy:
        algorithm = self._algorithms.get(algorithm_version_id)
        if algorithm is None or algorithm.project_id != project_id:
            raise ApplicationError("ALGORITHM_NOT_FOUND", "算法版本不存在", 404)
        if min_replicas < 0 or max_replicas < max(1, min_replicas):
            raise ApplicationError("SCALING_POLICY_INVALID", "副本范围不合法")
        policy = AutoscalingPolicy(
            algorithm_version_id,
            min_replicas,
            max_replicas,
            target_concurrency,
            idle_seconds,
            datetime.now(UTC),
        )
        if self._database is None:
            with self._lock:
                self._policies[algorithm_version_id] = policy
        else:
            with self._database.connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO autoscaling_policies (
                        algorithm_version_id, min_replicas, max_replicas,
                        target_concurrency, idle_seconds, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (algorithm_version_id) DO UPDATE SET
                        min_replicas = EXCLUDED.min_replicas,
                        max_replicas = EXCLUDED.max_replicas,
                        target_concurrency = EXCLUDED.target_concurrency,
                        idle_seconds = EXCLUDED.idle_seconds,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        policy.algorithm_version_id, policy.min_replicas,
                        policy.max_replicas, policy.target_concurrency,
                        policy.idle_seconds, policy.updated_at,
                    ),
                )
        with self._lock:
            self._last_active_at.setdefault(algorithm_version_id, policy.updated_at)
        return policy

    def list_policies(self, project_id: UUID | None = None) -> list[AutoscalingPolicy]:
        if self._database is None:
            with self._lock:
                policies = list(self._policies.values())
        else:
            with self._database.connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT * FROM autoscaling_policies ORDER BY algorithm_version_id")
                policies = [
                    AutoscalingPolicy(
                        row["algorithm_version_id"],
                        int(row["min_replicas"]),
                        int(row["max_replicas"]),
                        int(row["target_concurrency"]),
                        int(row["idle_seconds"]),
                        row["updated_at"],
                    )
                    for row in cursor.fetchall()
                ]
        if project_id is not None:
            policies = [
                item for item in policies
                if (algorithm := self._algorithms.get(item.algorithm_version_id)) is not None
                and algorithm.project_id == project_id
            ]
        return sorted(policies, key=lambda item: str(item.algorithm_version_id))

    def reconcile(self, project_id: UUID | None = None) -> dict[str, object]:
        active_counts = self._tasks.count_by_algorithm(
            (TaskStatus.QUEUED, TaskStatus.PREPARING, TaskStatus.STARTING, TaskStatus.RUNNING)
        )
        outcomes = []
        now = datetime.now(UTC)
        for policy in self.list_policies(project_id):
            active = active_counts.get(policy.algorithm_version_id, 0)
            desired = max(
                policy.min_replicas,
                min(policy.max_replicas, math.ceil(active / policy.target_concurrency)),
            )
            if active:
                with self._lock:
                    self._last_active_at[policy.algorithm_version_id] = now
            else:
                with self._lock:
                    last_active = self._last_active_at.get(
                        policy.algorithm_version_id,
                        policy.updated_at,
                    )
                algorithm = self._algorithms.get(policy.algorithm_version_id)
                current_count = sum(
                    instance.algorithm_version_id == str(policy.algorithm_version_id)
                    for instance in self._instances.list_instances(
                        algorithm.project_id if algorithm is not None else None
                    )
                )
                if (now - last_active).total_seconds() < policy.idle_seconds:
                    desired = max(desired, current_count)
            try:
                instances = self._instances.ensure_replicas(
                    policy.algorithm_version_id,
                    desired,
                    self._algorithms.get(policy.algorithm_version_id).project_id,
                )
                outcomes.append(
                    {
                        "algorithm_version_id": str(policy.algorithm_version_id),
                        "active_tasks": active,
                        "desired_replicas": desired,
                        "actual_replicas": len(instances),
                        "status": "reconciled",
                    }
                )
            except ApplicationError as exc:
                outcomes.append(
                    {
                        "algorithm_version_id": str(policy.algorithm_version_id),
                        "active_tasks": active,
                        "desired_replicas": desired,
                        "actual_replicas": None,
                        "status": "failed",
                        "error_code": exc.code,
                    }
                )
        return {"reconciled_at": datetime.now(UTC).isoformat(), "items": outcomes}

    def metrics(
        self,
        project_id: UUID | None = None,
        *,
        deployment_id: UUID | None = None,
        window_seconds: int = 60,
    ) -> dict[str, object]:
        counts = {
            status.value: total
            for status, total in self._tasks.count_by_status(project_id).items()
        }
        now = datetime.now(UTC)
        tasks = self._tasks.query(
            TaskQuery(project_id=project_id, deployment_id=deployment_id, limit=5000)
        )
        summary = _summarize_tasks(tasks, now=now, window_seconds=window_seconds)
        by_deployment: dict[str, dict[str, object]] = {}
        grouped: dict[UUID, list[InferenceTask]] = {}
        for item in tasks:
            if item.deployment_id is not None:
                grouped.setdefault(item.deployment_id, []).append(item)
        for dep_id, dep_tasks in grouped.items():
            dep_summary = _summarize_tasks(dep_tasks, now=now, window_seconds=window_seconds)
            if self._deployments is not None:
                dep = self._deployments.get(dep_id)
                if dep is not None:
                    dep_summary["algorithm_version_id"] = str(dep.algorithm_version_id)
                    dep_summary["node_id"] = dep.node_id
                    dep_summary["status"] = dep.status.value
            by_deployment[str(dep_id)] = dep_summary

        return {
            "tasks_by_status": counts,
            **summary,
            "deployment_metrics": by_deployment,
            "autoscaling_policies": len(self.list_policies(project_id)),
            "runtime_instances": sum(
                self._instance_in_project(instance.algorithm_version_id, project_id)
                for instance in self._instances.list_instances(project_id)
            ),
        }

    def _instance_in_project(
        self,
        algorithm_version_id: str,
        project_id: UUID | None,
    ) -> bool:
        try:
            version_id = UUID(algorithm_version_id)
        except ValueError:
            return False
        algorithm = self._algorithms.get(version_id)
        return algorithm is not None and (
            project_id is None or algorithm.project_id == project_id
        )

    def choose_version(self, algorithm_key: str, routing_key: str, project_id: UUID):
        candidates = [
            item
            for item in self._algorithms.list()
            if item.manifest.id == algorithm_key
            and item.project_id == project_id
            and item.status.value == "available"
            and item.traffic_weight > 0
        ]
        if not candidates:
            raise ApplicationError("ALGORITHM_NOT_AVAILABLE", "算法当前没有可用版本", 409)
        slot = int(hashlib.sha256(routing_key.encode()).hexdigest()[:8], 16) % 100
        cumulative = 0
        for candidate in candidates:
            cumulative += candidate.traffic_weight
            if slot < cumulative:
                return candidate
        return candidates[-1]
