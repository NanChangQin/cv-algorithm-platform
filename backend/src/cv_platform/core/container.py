from dataclasses import dataclass

from ..modules.algorithms.application.service import BuildQueue
from ..modules.algorithms.domain.repositories import AlgorithmRepository, BuildJobRepository
from ..modules.algorithms.infrastructure.local_build_queue import LocalBuildQueue
from ..modules.algorithms.infrastructure.manager_build_gateway import ManagerBuildGateway
from ..modules.algorithms.infrastructure.memory_repository import (
    InMemoryAlgorithmRepository,
    InMemoryBuildJobRepository,
)
from ..modules.algorithms.infrastructure.package_storage import AlgorithmPackageStorage
from ..modules.algorithms.infrastructure.persistent_queue import PostgresBuildQueue
from ..modules.algorithms.infrastructure.postgres_repository import (
    PostgresAlgorithmRepository,
    PostgresBuildJobRepository,
)
from ..modules.assets.domain.repositories import AssetRepository
from ..modules.assets.infrastructure.local_storage import LocalAssetStorage
from ..modules.assets.infrastructure.memory_repository import InMemoryAssetRepository
from ..modules.assets.infrastructure.postgres_repository import PostgresAssetRepository
from ..modules.comparisons.infrastructure.memory_repository import (
    InMemoryComparisonRepository,
)
from ..modules.comparisons.infrastructure.postgres_repository import (
    PostgresComparisonRepository,
)
from ..modules.evaluations.repository import (
    InMemoryEvaluationRepository,
    PostgresEvaluationRepository,
)
from ..modules.instances.application.service import RuntimeInstanceService
from ..modules.media.postgres_service import PostgresMediaSourceService
from ..modules.media.runs import MediaRunService
from ..modules.media.service import InMemoryMediaSourceService
from ..modules.media.storage import LocalVideoStorage
from ..modules.media.worker_gateway import MediaWorkerGateway
from ..modules.operations.service import OperationsService
from ..modules.projects.postgres_service import PostgresProjectService
from ..modules.projects.service import InMemoryProjectService
from ..modules.scheduling.cluster_gateway import ClusterAlgorithmManagerGateway
from ..modules.security.postgres_service import PostgresAuditLog, PostgresAuthService
from ..modules.security.service import (
    InMemoryAuditLog,
    LocalAuthService,
    SlidingWindowRateLimiter,
)
from ..modules.tasks.domain.execution import TaskQueue
from ..modules.tasks.domain.repositories import TaskRepository
from ..modules.tasks.infrastructure.http_prediction_gateway import (
    HttpAlgorithmPredictionGateway,
)
from ..modules.tasks.infrastructure.local_queue import LocalTaskQueue
from ..modules.tasks.infrastructure.memory_repository import InMemoryTaskRepository
from ..modules.tasks.infrastructure.persistent_queue import PostgresTaskQueue
from ..modules.tasks.infrastructure.postgres_repository import PostgresTaskRepository
from ..modules.training.datasets import TrainingDatasetStorage
from ..modules.training.orchestrator import LeaderLock, TrainingOrchestrator
from ..modules.training.repository import (
    InMemoryTrainingRepository,
    PostgresTrainingRepository,
)
from ..modules.workflows.service import InMemoryWorkflowService
from .config import get_settings
from .database import Database


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    algorithms: AlgorithmRepository
    build_jobs: BuildJobRepository
    package_storage: AlgorithmPackageStorage
    build_gateway: ManagerBuildGateway
    build_queue: BuildQueue
    build_executor: LocalBuildQueue
    assets: AssetRepository
    asset_storage: LocalAssetStorage
    tasks: TaskRepository
    algorithm_manager: ClusterAlgorithmManagerGateway
    task_queue: TaskQueue
    task_executor: LocalTaskQueue
    comparisons: InMemoryComparisonRepository | PostgresComparisonRepository
    evaluations: InMemoryEvaluationRepository | PostgresEvaluationRepository
    auth: LocalAuthService | PostgresAuthService
    audit_log: InMemoryAuditLog | PostgresAuditLog
    rate_limiter: SlidingWindowRateLimiter
    media_sources: InMemoryMediaSourceService | PostgresMediaSourceService
    media_worker: MediaWorkerGateway
    media_runs: MediaRunService
    video_storage: LocalVideoStorage
    workflows: InMemoryWorkflowService
    operations: OperationsService
    projects: InMemoryProjectService | PostgresProjectService
    training: InMemoryTrainingRepository | PostgresTrainingRepository
    training_storage: TrainingDatasetStorage
    training_orchestrator: TrainingOrchestrator
    coordinator_lock: LeaderLock
    database: Database | None


def build_container() -> ApplicationContainer:
    settings = get_settings()
    database = None
    if settings.persistence_backend == "postgres":
        database = Database.from_settings(settings)
        if not database.is_healthy():
            raise RuntimeError("PostgreSQL persistence is configured but unavailable")
        auth = PostgresAuthService(
            database,
            settings.admin_username,
            settings.admin_password,
            settings.session_ttl_seconds,
        )
        projects = PostgresProjectService(database, auth.initial_admin_id)
        algorithms = PostgresAlgorithmRepository(database)
        build_jobs = PostgresBuildJobRepository(database)
        assets = PostgresAssetRepository(database)
        tasks = PostgresTaskRepository(database)
        comparisons = PostgresComparisonRepository(database)
        evaluations = PostgresEvaluationRepository(database)
        audit_log = PostgresAuditLog(database)
        media_sources = PostgresMediaSourceService(database)
        training: InMemoryTrainingRepository | PostgresTrainingRepository = (
            PostgresTrainingRepository(database)
        )
    else:
        algorithms = InMemoryAlgorithmRepository()
        build_jobs = InMemoryBuildJobRepository()
        assets = InMemoryAssetRepository()
        tasks = InMemoryTaskRepository()
        comparisons = InMemoryComparisonRepository()
        evaluations = InMemoryEvaluationRepository()
        auth = LocalAuthService(
            settings.admin_username,
            settings.admin_password,
            settings.session_ttl_seconds,
        )
        projects = InMemoryProjectService(auth.initial_admin_id)
        audit_log = InMemoryAuditLog()
        media_sources = InMemoryMediaSourceService()
        training = InMemoryTrainingRepository()
    media_worker = MediaWorkerGateway(settings.media_worker_url)
    manager_token = (
        settings.manager_api_token.get_secret_value() if settings.manager_api_token else None
    )
    manager = ClusterAlgorithmManagerGateway(
        settings.algorithm_manager_url, database, api_token=manager_token
    )
    build_gateway = ManagerBuildGateway(settings.algorithm_manager_url, api_token=manager_token)
    build_executor = LocalBuildQueue(algorithms, build_jobs, build_gateway)
    instances = RuntimeInstanceService(algorithms, manager)
    task_executor = LocalTaskQueue(
        tasks,
        algorithms,
        assets,
        instances,
        HttpAlgorithmPredictionGateway(
            settings.prediction_timeout_seconds,
            settings.storage_root,
            manager_api_token=manager_token,
        ),
        settings.task_worker_count,
    )
    # 与 Algorithm Manager 共用的数据目录里，节点自己使用 training/，主服务器使用 training-store/
    training_storage = TrainingDatasetStorage(
        settings.storage_root / "training-store",
        settings.max_dataset_bytes,
        settings.max_dataset_files,
    )
    if settings.queue_backend == "postgres":
        if database is None:
            raise RuntimeError("PostgreSQL queue requires PostgreSQL persistence")
        build_queue: BuildQueue = PostgresBuildQueue(database)
        task_queue: TaskQueue = PostgresTaskQueue(database)
    else:
        build_queue = build_executor
        task_queue = task_executor
    return ApplicationContainer(
        algorithms=algorithms,
        build_jobs=build_jobs,
        package_storage=AlgorithmPackageStorage(
            settings.package_root,
            settings.max_package_bytes,
            settings.max_package_extracted_bytes,
            settings.max_package_files,
        ),
        build_gateway=build_gateway,
        build_queue=build_queue,
        build_executor=build_executor,
        assets=assets,
        asset_storage=LocalAssetStorage(
            settings.storage_root,
            settings.algorithm_data_root,
            settings.max_upload_bytes,
            settings.max_image_pixels,
        ),
        tasks=tasks,
        algorithm_manager=manager,
        task_queue=task_queue,
        task_executor=task_executor,
        comparisons=comparisons,
        evaluations=evaluations,
        auth=auth,
        audit_log=audit_log,
        rate_limiter=SlidingWindowRateLimiter(
            settings.rate_limit_requests,
            settings.rate_limit_window_seconds,
        ),
        media_sources=media_sources,
        media_worker=media_worker,
        media_runs=MediaRunService(media_sources, media_worker, database),
        video_storage=LocalVideoStorage(
            settings.storage_root,
            settings.max_video_upload_bytes,
        ),
        workflows=InMemoryWorkflowService(algorithms, database),
        operations=OperationsService(algorithms, tasks, instances, database),
        projects=projects,
        training=training,
        training_storage=training_storage,
        training_orchestrator=TrainingOrchestrator(
            training, training_storage, manager, settings.training_image
        ),
        coordinator_lock=LeaderLock(database, "cv-platform-coordinator"),
        database=database,
    )
