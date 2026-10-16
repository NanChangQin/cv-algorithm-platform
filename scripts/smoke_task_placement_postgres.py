"""Verify placement SQL using a session-local temporary table, without business writes."""
from contextlib import contextmanager
from uuid import uuid4

from cv_algorithm_sdk import TaskStatus
from cv_platform.core.config import Settings
from cv_platform.core.database import Database
from cv_platform.modules.tasks.domain.entities import InferenceTask
from cv_platform.modules.tasks.infrastructure.postgres_repository import PostgresTaskRepository


def main() -> None:
    database = Database.from_settings(Settings())
    with database.connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TEMP TABLE inference_tasks "
                "(LIKE public.inference_tasks INCLUDING DEFAULTS)"
            )

        class SessionDatabase:
            @contextmanager
            def connect(self):
                yield connection

        repository = PostgresTaskRepository(SessionDatabase())
        task = InferenceTask.queued(
            uuid4(), "file:///data/test.jpg", {}, node_id="dell-gpu", gpu_device_ids=("0",)
        )
        repository.add(task)
        persisted = repository.get(task.id)
        assert persisted.node_id == "dell-gpu"
        assert persisted.gpu_device_ids == ("0",)
        running = persisted.transition(
            TaskStatus.RUNNING, container_id="example-container",
            execution_node_id="dell-gpu", execution_gpu_device_ids=("0",),
        )
        repository.save(running)
        reloaded = repository.get(task.id)
        assert reloaded.execution_node_id == "dell-gpu"
        assert reloaded.execution_gpu_device_ids == ("0",)
        assert reloaded.node_id == "dell-gpu"
        assert reloaded.container_id == "example-container"
        print("PostgreSQL requested/actual placement round trip passed (temporary table only)")


if __name__ == "__main__":
    main()
