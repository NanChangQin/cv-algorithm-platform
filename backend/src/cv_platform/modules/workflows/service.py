from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event, RLock
from time import sleep
from uuid import UUID

from cv_algorithm_sdk import TaskStatus
from psycopg.types.json import Jsonb

from ...core.database import Database
from ...core.errors import ApplicationError
from ...core.project_context import DEFAULT_PROJECT_ID
from ..algorithms.domain.repositories import AlgorithmRepository
from ..tasks.application.service import TaskService
from .domain import (
    Workflow,
    WorkflowMode,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStatus,
)


class InMemoryWorkflowService:
    def __init__(
        self,
        algorithms: AlgorithmRepository,
        database: Database | None = None,
    ) -> None:
        self._algorithms = algorithms
        self._database = database
        self._items: dict[UUID, Workflow] = {}
        self._runs: dict[UUID, WorkflowRun] = {}
        self._cancel_events: dict[UUID, Event] = {}
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cv-workflow")
        self._lock = RLock()

    def create(
        self,
        name: str,
        mode: WorkflowMode,
        nodes: list[WorkflowNode],
        project_id: UUID = DEFAULT_PROJECT_ID,
        actor: str = "local-admin",
    ) -> Workflow:
        self._validate(nodes)
        if any(
            self._algorithms.get(node.algorithm_version_id).project_id != project_id
            for node in nodes
        ):
            raise ApplicationError("ALGORITHM_NOT_FOUND", "工作流引用的算法不存在", 404)
        workflow = Workflow.create(name.strip(), mode, nodes, project_id, actor)
        if self._database is None:
            with self._lock:
                self._items[workflow.id] = workflow
        else:
            with self._database.connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO workflows (
                        id, project_id, name, mode, created_by,
                        created_by_label, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, NULL, %s, %s, %s)
                    """,
                    (
                        workflow.id, workflow.project_id, workflow.name,
                        workflow.mode.value, workflow.created_by,
                        workflow.created_at, workflow.created_at,
                    ),
                )
                for position, node in enumerate(workflow.nodes):
                    cursor.execute(
                        """
                        INSERT INTO workflow_nodes (
                            workflow_id, node_key, algorithm_version_id, parameters, position
                        ) VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            workflow.id, node.id, node.algorithm_version_id,
                            Jsonb(node.parameters), position,
                        ),
                    )
                for node in workflow.nodes:
                    for dependency in node.depends_on:
                        cursor.execute(
                            """
                            INSERT INTO workflow_node_dependencies (
                                workflow_id, node_key, depends_on_key
                            ) VALUES (%s, %s, %s)
                            """,
                            (workflow.id, node.id, dependency),
                        )
        return workflow

    def list(self, project_id: UUID | None = None) -> list[Workflow]:
        if self._database is None:
            with self._lock:
                return sorted(
                    [
                        item for item in self._items.values()
                        if project_id is None or item.project_id == project_id
                    ],
                    key=lambda item: item.created_at,
                    reverse=True,
                )
        with self._database.connect() as connection, connection.cursor() as cursor:
            if project_id is None:
                cursor.execute("SELECT id FROM workflows ORDER BY created_at DESC")
            else:
                cursor.execute(
                    "SELECT id FROM workflows WHERE project_id = %s ORDER BY created_at DESC",
                    (project_id,),
                )
            workflow_ids = [row["id"] for row in cursor.fetchall()]
        return [self.get(workflow_id) for workflow_id in workflow_ids]

    def get(self, workflow_id: UUID, project_id: UUID | None = None) -> Workflow:
        if self._database is None:
            with self._lock:
                workflow = self._items.get(workflow_id)
        else:
            workflow = self._get_postgres_workflow(workflow_id)
        if workflow is None or (project_id is not None and workflow.project_id != project_id):
            raise ApplicationError("WORKFLOW_NOT_FOUND", "算法工作流不存在", 404)
        return workflow

    def start(
        self,
        workflow_id: UUID,
        asset_id: UUID,
        tasks: TaskService,
        project_id: UUID = DEFAULT_PROJECT_ID,
    ) -> WorkflowRun:
        self.get(workflow_id, project_id)
        run = WorkflowRun.queued(workflow_id, asset_id, project_id)
        if self._database is None:
            with self._lock:
                self._runs[run.id] = run
        else:
            with self._database.connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO workflow_runs (
                        id, project_id, workflow_id, asset_id, status,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run.id, run.project_id, run.workflow_id, run.asset_id,
                        run.status.value, run.created_at, run.updated_at,
                    ),
                )
        with self._lock:
            self._cancel_events[run.id] = Event()
        self._executor.submit(self._execute, run.id, tasks)
        return run

    def get_run(self, run_id: UUID, project_id: UUID | None = None) -> WorkflowRun:
        if self._database is None:
            with self._lock:
                run = self._runs.get(run_id)
        else:
            with self._database.connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT * FROM workflow_runs WHERE id = %s", (run_id,))
                row = cursor.fetchone()
                if row:
                    cursor.execute(
                        "SELECT node_key, task_id FROM workflow_run_tasks "
                        "WHERE workflow_run_id = %s",
                        (run_id,),
                    )
                    node_tasks = {
                        str(item["node_key"]): item["task_id"] for item in cursor.fetchall()
                    }
                    run = WorkflowRun(
                        id=row["id"], workflow_id=row["workflow_id"],
                        asset_id=row["asset_id"], status=WorkflowRunStatus(str(row["status"])),
                        node_tasks=node_tasks, created_at=row["created_at"],
                        updated_at=row["updated_at"], error_message=row["error_message"],
                        project_id=row["project_id"],
                    )
                else:
                    run = None
        if run is None or (project_id is not None and run.project_id != project_id):
            raise ApplicationError("WORKFLOW_RUN_NOT_FOUND", "工作流运行不存在", 404)
        return run

    def list_runs(self, project_id: UUID | None = None) -> list[WorkflowRun]:
        if self._database is None:
            with self._lock:
                return sorted(
                    [
                        item for item in self._runs.values()
                        if project_id is None or item.project_id == project_id
                    ],
                    key=lambda item: item.created_at,
                    reverse=True,
                )
        with self._database.connect() as connection, connection.cursor() as cursor:
            if project_id is None:
                cursor.execute("SELECT id FROM workflow_runs ORDER BY created_at DESC")
            else:
                cursor.execute(
                    "SELECT id FROM workflow_runs WHERE project_id = %s ORDER BY created_at DESC",
                    (project_id,),
                )
            run_ids = [row["id"] for row in cursor.fetchall()]
        return [self.get_run(run_id) for run_id in run_ids]

    def cancel(self, run_id: UUID, tasks: TaskService) -> WorkflowRun:
        run = self.get_run(run_id)
        with self._lock:
            event = self._cancel_events.setdefault(run_id, Event())
            event.set()
        for task_id in run.node_tasks.values():
            task = tasks.get_task(task_id)
            if task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                tasks.cancel(task_id)
        return self._update_run(run, WorkflowRunStatus.CANCELLED)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def recover_incomplete(self) -> None:
        for run in self.list_runs():
            if run.status in {WorkflowRunStatus.QUEUED, WorkflowRunStatus.RUNNING}:
                self._update_run(
                    run,
                    WorkflowRunStatus.FAILED,
                    "平台重启导致工作流中断",
                )

    def _execute(self, run_id: UUID, tasks: TaskService) -> None:
        run = self.get_run(run_id)
        workflow = self.get(run.workflow_id)
        run = self._update_run(run, WorkflowRunStatus.RUNNING)
        pending = {node.id: node for node in workflow.nodes}
        completed: set[str] = set()
        try:
            while pending:
                if self._cancel_events[run_id].is_set():
                    return
                ready = [
                    node
                    for node in pending.values()
                    if set(node.depends_on).issubset(completed)
                ]
                if workflow.mode is WorkflowMode.SEQUENTIAL:
                    ready = ready[:1]
                node_tasks = {
                    node.id: tasks.create_task(
                        node.algorithm_version_id,
                        node.parameters,
                        asset_id=run.asset_id,
                    )
                    for node in ready
                }
                run = replace(
                    run,
                    node_tasks={
                        **run.node_tasks,
                        **{node_id: task.id for node_id, task in node_tasks.items()},
                    },
                    updated_at=datetime.now(UTC),
                )
                self._save_run(run)
                terminal = set()
                while len(terminal) < len(node_tasks):
                    if self._cancel_events[run_id].is_set():
                        return
                    for node_id, task in node_tasks.items():
                        current = tasks.get_task(task.id)
                        if current.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
                            raise RuntimeError(
                                current.error_message or f"workflow node {node_id} failed"
                            )
                        if current.status is TaskStatus.COMPLETED:
                            terminal.add(node_id)
                    if len(terminal) < len(node_tasks):
                        sleep(0.25)
                for node_id in terminal:
                    completed.add(node_id)
                    pending.pop(node_id)
            self._update_run(run, WorkflowRunStatus.COMPLETED)
        except Exception as exc:
            self._update_run(run, WorkflowRunStatus.FAILED, str(exc))

    def _update_run(
        self,
        run: WorkflowRun,
        status: WorkflowRunStatus,
        error_message: str | None = None,
    ) -> WorkflowRun:
        updated = replace(
            run,
            status=status,
            error_message=error_message,
            updated_at=datetime.now(UTC),
        )
        self._save_run(updated)
        return updated

    def _save_run(self, run: WorkflowRun) -> None:
        if self._database is None:
            with self._lock:
                self._runs[run.id] = run
            return
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE workflow_runs SET status = %s, error_message = %s,
                    updated_at = %s WHERE id = %s
                """,
                (run.status.value, run.error_message, run.updated_at, run.id),
            )
            cursor.execute("DELETE FROM workflow_run_tasks WHERE workflow_run_id = %s", (run.id,))
            for node_key, task_id in run.node_tasks.items():
                cursor.execute(
                    """
                    INSERT INTO workflow_run_tasks (workflow_run_id, node_key, task_id)
                    VALUES (%s, %s, %s)
                    """,
                    (run.id, node_key, task_id),
                )

    def _get_postgres_workflow(self, workflow_id: UUID) -> Workflow | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM workflows WHERE id = %s", (workflow_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                """
                SELECT n.*, COALESCE(array_agg(d.depends_on_key)
                    FILTER (WHERE d.depends_on_key IS NOT NULL), '{}') AS dependencies
                FROM workflow_nodes n
                LEFT JOIN workflow_node_dependencies d
                    ON d.workflow_id = n.workflow_id AND d.node_key = n.node_key
                WHERE n.workflow_id = %s
                GROUP BY n.workflow_id, n.node_key ORDER BY n.position
                """,
                (workflow_id,),
            )
            nodes = tuple(
                WorkflowNode(
                    id=str(item["node_key"]),
                    algorithm_version_id=item["algorithm_version_id"],
                    parameters=dict(item["parameters"] or {}),
                    depends_on=tuple(str(value) for value in item["dependencies"]),
                )
                for item in cursor.fetchall()
            )
            return Workflow(
                id=row["id"], name=str(row["name"]),
                mode=WorkflowMode(str(row["mode"])), nodes=nodes,
                created_at=row["created_at"], created_by=str(row["created_by_label"]),
                project_id=row["project_id"],
            )

    def _validate(self, nodes: list[WorkflowNode]) -> None:
        if not nodes or len(nodes) > 32:
            raise ApplicationError("WORKFLOW_INVALID", "工作流需要 1 到 32 个节点")
        node_ids = [node.id for node in nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ApplicationError("WORKFLOW_INVALID", "工作流节点 ID 必须唯一")
        dependencies = {node.id: set(node.depends_on) for node in nodes}
        if any(not values.issubset(node_ids) for values in dependencies.values()):
            raise ApplicationError("WORKFLOW_INVALID", "工作流依赖节点不存在")
        if any(node.id in dependencies[node.id] for node in nodes):
            raise ApplicationError("WORKFLOW_INVALID", "工作流节点不能依赖自身")
        pending = {key: set(value) for key, value in dependencies.items()}
        while pending:
            ready = {key for key, values in pending.items() if not values}
            if not ready:
                raise ApplicationError("WORKFLOW_CYCLE", "工作流存在循环依赖")
            pending = {
                key: values - ready
                for key, values in pending.items()
                if key not in ready
            }
        for node in nodes:
            algorithm = self._algorithms.get(node.algorithm_version_id)
            if algorithm is None:
                raise ApplicationError("ALGORITHM_NOT_FOUND", "工作流引用的算法不存在", 404)
            try:
                algorithm.manifest.resolve_parameters(node.parameters)
            except ValueError as exc:
                raise ApplicationError("PARAMETER_INVALID", str(exc)) from exc
