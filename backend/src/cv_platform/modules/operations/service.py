from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from uuid import UUID
import hashlib
import math

from cv_algorithm_sdk import TaskStatus

from ...core.database import Database
from ...core.errors import ApplicationError
from ..algorithms.domain.repositories import AlgorithmRepository
from ..tasks.domain.repositories import TaskRepository
from ..instances.application.service import RuntimeInstanceService


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
    ) -> None:
        self._algorithms = algorithms
        self._tasks = tasks
        self._instances = instances
        self._database = database
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
                        algorithm_version_id=row["algorithm_version_id"],
                        min_replicas=int(row["min_replicas"]),
                        max_replicas=int(row["max_replicas"]),
                        target_concurrency=int(row["target_concurrency"]),
                        idle_seconds=int(row["idle_seconds"]),
                        updated_at=row["updated_at"],
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

    def metrics(self, project_id: UUID | None = None) -> dict[str, object]:
        counts = {
            status.value: total
            for status, total in self._tasks.count_by_status(project_id).items()
        }
        average, samples = self._tasks.inference_time_stats(project_id)
        return {
            "tasks_by_status": counts,
            "average_inference_ms": round(average, 3),
            "completed_samples": samples,
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
