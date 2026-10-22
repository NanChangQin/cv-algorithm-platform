from __future__ import annotations

import logging
import signal
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event

from .core.config import get_settings
from .core.container import build_container
from .modules.tasks.infrastructure.persistent_queue import PostgresWorkClaimer

logger = logging.getLogger(__name__)


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    if settings.queue_backend != "postgres":
        raise RuntimeError("durable worker requires CV_PLATFORM_QUEUE_BACKEND=postgres")
    container = build_container()
    if container.database is None:
        raise RuntimeError("durable worker requires PostgreSQL persistence")

    stopped = Event()

    def stop(*_: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    claimer = PostgresWorkClaimer(container.database)

    # Only one durable dispatcher is needed on the current single-GPU server.
    # SKIP LOCKED still makes individual claims safe if this is expanded later.
    # 会话级锁，用独立连接持有；进程退出或连接关闭时自动释放
    with container.database.dedicated() as lock_connection, lock_connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_lock(hashtext('cv-platform-durable-worker')) AS locked"
        )
        if not bool(cursor.fetchone()["locked"]):
            raise RuntimeError("another durable worker already owns the dispatcher lock")

        recovered_tasks, recovered_builds = claimer.recover_interrupted()
        logger.info(
            "durable worker started; recovered_tasks=%s recovered_builds=%s",
            recovered_tasks,
            recovered_builds,
        )
        _work_loop(
            container, claimer, stopped, settings.task_worker_count, settings.worker_poll_seconds
        )


def _work_loop(container, claimer, stopped: Event, task_workers: int, poll_seconds: float) -> None:
    task_pool = ThreadPoolExecutor(max_workers=task_workers, thread_name_prefix="durable-inference")
    build_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="durable-build")
    task_futures: set[Future[None]] = set()
    build_future: Future[None] | None = None
    try:
        while not stopped.is_set():
            task_futures = {future for future in task_futures if not future.done()}
            while len(task_futures) < task_workers:
                task_id = claimer.claim_task()
                if task_id is None:
                    break
                task_futures.add(task_pool.submit(container.task_executor.execute_now, task_id))

            if build_future is None or build_future.done():
                build_id = claimer.claim_build()
                build_future = (
                    build_pool.submit(container.build_executor.execute_now, build_id)
                    if build_id is not None
                    else None
                )
            stopped.wait(poll_seconds)
    finally:
        task_pool.shutdown(wait=False, cancel_futures=True)
        build_pool.shutdown(wait=False, cancel_futures=True)
        container.task_executor.shutdown()
        container.build_executor.shutdown()


if __name__ == "__main__":
    run()
