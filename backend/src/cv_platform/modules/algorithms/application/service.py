from typing import BinaryIO, Protocol
from uuid import UUID

from cv_algorithm_sdk import AlgorithmStatus

from ....core.errors import ApplicationError
from ...tasks.domain.repositories import TaskRepository
from ..domain.entities import AlgorithmVersion, BuildJob
from ..domain.repositories import AlgorithmRepository, BuildJobRepository
from ..infrastructure.manager_build_gateway import ManagerBuildGateway
from ...instances.domain.gateway import AlgorithmManagerGateway
from ....core.project_context import DEFAULT_PROJECT_ID
from ..infrastructure.package_storage import AlgorithmPackageStorage


class BuildQueue(Protocol):
    def submit(self, job_id: UUID) -> None: ...

    def shutdown(self) -> None: ...


class AlgorithmService:
    def __init__(
        self,
        repository: AlgorithmRepository,
        jobs: BuildJobRepository | None = None,
        packages: AlgorithmPackageStorage | None = None,
        build_queue: BuildQueue | None = None,
        tasks: TaskRepository | None = None,
        build_gateway: ManagerBuildGateway | None = None,
        instance_gateway: AlgorithmManagerGateway | None = None,
        project_id: UUID = DEFAULT_PROJECT_ID,
        actor: str = "local-admin",
    ) -> None:
        self._repository = repository
        self._jobs = jobs
        self._packages = packages
        self._build_queue = build_queue
        self._tasks = tasks
        self._build_gateway = build_gateway
        self._instance_gateway = instance_gateway
        self._project_id = project_id
        self._actor = actor

    def list_algorithms(self) -> list[AlgorithmVersion]:
        return [item for item in self._repository.list() if item.project_id == self._project_id]

    def get_algorithm(self, algorithm_id: UUID) -> AlgorithmVersion:
        algorithm = self._repository.get(algorithm_id)
        if algorithm is None or algorithm.project_id != self._project_id:
            raise ApplicationError("ALGORITHM_NOT_FOUND", "算法版本不存在", 404)
        return algorithm

    def import_package(self, stream: BinaryIO, filename: str) -> AlgorithmVersion:
        if self._packages is None:
            raise ApplicationError("IMPORT_UNAVAILABLE", "算法导入服务未配置", 503)
        manifest, package_path, sha256 = self._packages.import_package(stream, filename)
        if any(
            item.manifest.id == manifest.id and item.manifest.version == manifest.version
            for item in self.list_algorithms()
        ):
            raise ApplicationError("ALGORITHM_VERSION_EXISTS", "算法 ID 与版本已存在", 409)
        # 语义化版本允许 “+构建元数据”，但 Docker 镜像标签不允许 “+”
        image = (
            f"cv-platform/{self._project_id.hex[:12]}-"
            f"{manifest.id}:{manifest.version.replace('+', '_')}"
        )
        algorithm = AlgorithmVersion.uploaded(
            manifest,
            image,
            package_path,
            sha256,
            self._project_id,
            self._actor,
        )
        try:
            self._repository.add(algorithm)
        except ValueError as exc:
            raise ApplicationError(
                "ALGORITHM_VERSION_EXISTS",
                "算法 ID 与版本已存在",
                409,
            ) from exc
        return algorithm

    def build(self, algorithm_id: UUID) -> BuildJob:
        algorithm = self.get_algorithm(algorithm_id)
        if algorithm.status not in {AlgorithmStatus.UPLOADED, AlgorithmStatus.FAILED}:
            raise ApplicationError("BUILD_NOT_ALLOWED", "当前算法状态不可构建", 409)
        if self._jobs is None or self._build_queue is None:
            raise ApplicationError("BUILD_UNAVAILABLE", "算法构建服务未配置", 503)
        job = BuildJob.queued(algorithm.id)
        self._jobs.add(job)
        self._repository.save(algorithm.with_status(AlgorithmStatus.VALIDATING))
        self._build_queue.submit(job.id)
        return job

    def get_build_job(self, job_id: UUID) -> BuildJob:
        job = self._jobs.get(job_id) if self._jobs else None
        if job is None:
            raise ApplicationError("BUILD_JOB_NOT_FOUND", "构建任务不存在", 404)
        self.get_algorithm(job.algorithm_version_id)
        return job

    def set_enabled(self, algorithm_id: UUID, enabled: bool) -> AlgorithmVersion:
        algorithm = self.get_algorithm(algorithm_id)
        if enabled and algorithm.status is not AlgorithmStatus.DISABLED:
            raise ApplicationError("ENABLE_NOT_ALLOWED", "仅已停用版本可以启用", 409)
        if not enabled and algorithm.status is not AlgorithmStatus.AVAILABLE:
            raise ApplicationError("DISABLE_NOT_ALLOWED", "仅可用版本可以停用", 409)
        updated = algorithm.with_status(
            AlgorithmStatus.AVAILABLE if enabled else AlgorithmStatus.DISABLED
        )
        self._repository.save(updated)
        return updated

    def rollback(self, algorithm_id: UUID) -> list[AlgorithmVersion]:
        target = self.get_algorithm(algorithm_id)
        if target.status not in {AlgorithmStatus.AVAILABLE, AlgorithmStatus.DISABLED}:
            raise ApplicationError("ROLLBACK_NOT_ALLOWED", "目标版本尚不可用", 409)
        updated = []
        for algorithm in self.list_algorithms():
            if algorithm.manifest.id != target.manifest.id:
                continue
            desired = (
                AlgorithmStatus.AVAILABLE
                if algorithm.id == target.id
                else AlgorithmStatus.DISABLED
            )
            if algorithm.status in {AlgorithmStatus.AVAILABLE, AlgorithmStatus.DISABLED}:
                algorithm = algorithm.with_status(desired)
                self._repository.save(algorithm)
            updated.append(algorithm)
        return updated

    def delete(self, algorithm_id: UUID, *, remove_image: bool = False) -> None:
        algorithm = self.get_algorithm(algorithm_id)
        if self._tasks and any(
            task.algorithm_version_id == algorithm_id for task in self._tasks.list()
        ):
            raise ApplicationError("ALGORITHM_IN_USE", "算法版本已有任务引用，不能删除", 409)
        if self._instance_gateway is not None:
            for instance in self._instance_gateway.list_instances():
                if instance.algorithm_version_id == str(algorithm_id):
                    self._instance_gateway.remove(instance.id)
        if remove_image:
            if self._build_gateway is None:
                raise ApplicationError("IMAGE_REMOVE_UNAVAILABLE", "镜像删除服务未配置", 503)
            self._build_gateway.remove_image(algorithm.image)
        self._repository.delete(algorithm_id)
