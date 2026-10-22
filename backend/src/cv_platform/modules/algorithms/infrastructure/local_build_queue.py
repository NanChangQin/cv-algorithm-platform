import logging
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from cv_algorithm_sdk import AlgorithmStatus

from ....core.errors import ApplicationError
from ..domain.entities import BuildStatus
from ..domain.repositories import AlgorithmRepository, BuildJobRepository
from .manager_build_gateway import ManagerBuildGateway

logger = logging.getLogger(__name__)


class LocalBuildQueue:
    def __init__(
        self,
        algorithms: AlgorithmRepository,
        jobs: BuildJobRepository,
        gateway: ManagerBuildGateway,
    ) -> None:
        self._algorithms = algorithms
        self._jobs = jobs
        self._gateway = gateway
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cv-build")

    def submit(self, job_id: UUID) -> None:
        self._executor.submit(self._execute, job_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def execute_now(self, job_id: UUID) -> None:
        """Execute a build claimed by the durable worker."""
        self._execute(job_id)

    def _execute(self, job_id: UUID) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        algorithm = self._algorithms.get(job.algorithm_version_id)
        if algorithm is None or not algorithm.package_path:
            self._jobs.save(job.update(BuildStatus.FAILED, "算法包不存在"))
            return
        try:
            job = job.update(BuildStatus.BUILDING, "开始使用受控运行时模板构建镜像")
            self._jobs.save(job)
            self._algorithms.save(algorithm.with_status(AlgorithmStatus.BUILDING))
            digest, build_logs = self._gateway.build(
                algorithm.package_path,
                algorithm.image,
                algorithm.manifest.runtime.framework,
            )
            for line in build_logs:
                job = job.update(BuildStatus.BUILDING, line)
            job = job.update(BuildStatus.TESTING, "镜像构建完成，等待首次容器协议验收")
            self._jobs.save(job)
            protocol_logs = self._gateway.validate_protocol(
                algorithm_version_id=str(algorithm.id),
                algorithm_key=algorithm.manifest.id,
                version=algorithm.manifest.version,
                image=algorithm.image,
                framework_device=algorithm.manifest.runtime.device.value,
                memory_mb=algorithm.manifest.runtime.min_memory_mb,
                expected_output=algorithm.manifest.output.type.value,
            )
            for line in protocol_logs:
                job = job.update(BuildStatus.TESTING, line)
            self._algorithms.save(
                algorithm.with_status(AlgorithmStatus.AVAILABLE, image_digest=digest)
            )
            self._jobs.save(
                job.update(
                    BuildStatus.COMPLETED,
                    "构建完成，算法版本已发布",
                    image_digest=digest,
                )
            )
        except ApplicationError as exc:
            self._fail(job, algorithm, exc.message)
        except Exception:
            logger.exception("algorithm build failed", extra={"job_id": str(job_id)})
            self._fail(job, algorithm, "算法镜像构建失败")

    def _fail(self, job, algorithm, message: str) -> None:
        self._algorithms.save(algorithm.with_status(AlgorithmStatus.FAILED))
        self._jobs.save(
            job.update(BuildStatus.FAILED, message, error_message=message)
        )
