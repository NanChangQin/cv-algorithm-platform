import asyncio
import time
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from uuid import uuid4

from cv_algorithm_sdk import TaskStatus
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .api.router import api_router
from .core.config import get_settings
from .core.errors import install_exception_handlers
from .core.metrics import METRICS
from .core.seed import seed_demo_data
from .dependencies import get_container
from .modules.algorithms.application.manifest_order import restore_manifest_order
from .modules.tasks.domain.repositories import TaskQuery


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        container = get_container()
        if settings.queue_backend == "local":
            interrupted = TaskQuery(statuses=(
                TaskStatus.QUEUED,
                TaskStatus.PREPARING,
                TaskStatus.STARTING,
                TaskStatus.RUNNING,
            ))
            for task in container.tasks.query(interrupted):
                container.tasks.save(
                    task.transition(
                        TaskStatus.FAILED,
                        error_code="TASK_INTERRUPTED",
                        error_message="平台重启导致任务中断",
                    )
                )
        container.media_runs.recover_incomplete()
        container.workflows.recover_incomplete()
        seed_demo_data(container)
        restore_manifest_order(container.algorithms)
        async def reconcile_autoscaling() -> None:
            while True:
                await asyncio.to_thread(container.operations.reconcile)
                await asyncio.sleep(settings.autoscaling_reconcile_seconds)

        async def coordinate_background_work() -> None:
            # 多个 Backend 进程时只有持有锁的一个负责推进训练
            while True:
                if container.coordinator_lock.held():
                    await asyncio.to_thread(container.training_orchestrator.tick)
                    await asyncio.to_thread(container.deployment_orchestrator.tick)
                await asyncio.sleep(settings.coordinator_interval_seconds)

        background_tasks = [
            asyncio.create_task(reconcile_autoscaling()),
            asyncio.create_task(coordinate_background_work()),
        ]
        try:
            yield
        finally:
            for task in background_tasks:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            container.training_orchestrator.shutdown()
            container.deployment_orchestrator.shutdown()
            container.coordinator_lock.release()
            container.task_queue.shutdown()
            container.build_queue.shutdown()
            if container.task_executor is not container.task_queue:
                container.task_executor.shutdown()
            if container.build_executor is not container.build_queue:
                container.build_executor.shutdown()
            container.workflows.shutdown()
            container.media_runs.shutdown()
            if container.database is not None:
                container.database.close()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Total-Count"],
    )
    install_exception_handlers(app)
    public_paths = {"/health", f"{settings.api_prefix}/auth/login"}
    rate_limited_paths = {
        f"{settings.api_prefix}/assets/upload",
        f"{settings.api_prefix}/algorithms/import",
        f"{settings.api_prefix}/training/datasets",
        f"{settings.api_prefix}/training/runs",
        f"{settings.api_prefix}/tasks",
        f"{settings.api_prefix}/comparisons",
        f"{settings.api_prefix}/evaluations/runs",
        f"{settings.api_prefix}/auth/login",
    }
    admin_only_prefixes = (
        f"{settings.api_prefix}/system",
        f"{settings.api_prefix}/users",
        f"{settings.api_prefix}/operations",
        f"{settings.api_prefix}/runtime-nodes",
        f"{settings.api_prefix}/instances",
    )
    developer_prefixes = (
        f"{settings.api_prefix}/training",
        f"{settings.api_prefix}/algorithms/import",
        f"{settings.api_prefix}/build-jobs",
        f"{settings.api_prefix}/deployment-manifests",
    )

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        request_started = time.perf_counter()
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        request.state.request_id = request_id
        is_api = request.url.path.startswith(settings.api_prefix)
        # 免登录模式下没有 Cookie 可依赖 SameSite 保护：任何网页都能借用户的浏览器向内网平台
        # 提交表单（上传文件、导入算法包）。浏览器会为跨站请求带上 Sec-Fetch-Site: cross-site，
        # 平台前端自身是 same-origin，curl 等非浏览器客户端不带该头，均不受影响。
        if (
            is_api
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.headers.get("Sec-Fetch-Site") == "cross-site"
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "code": "CROSS_SITE_REQUEST",
                    "message": "拒绝来自其他网站的写操作请求",
                    "request_id": request_id,
                },
            )
        authorization = request.headers.get("Authorization", "")
        header_token = authorization[7:] if authorization.startswith("Bearer ") else None
        token = header_token or request.cookies.get("cv_session")
        request.state.auth_token = token
        container = get_container()
        if settings.auth_enabled:
            session = container.auth.authenticate(token)
        else:
            from .modules.security.service import Session, UserRole

            session = Session(
                token="",
                user_id=container.auth.initial_admin_id,
                username=settings.admin_username.strip().lower(),
                role=UserRole.ADMIN,
                expires_at=datetime.max.replace(tzinfo=UTC),
            )
        request.state.session = session
        actor = session.username if session else "anonymous"
        if is_api and request.url.path not in public_paths and session is None:
            return JSONResponse(
                status_code=401,
                content={"code": "AUTH_REQUIRED", "message": "请先登录", "request_id": request_id},
            )
        if session is not None:
            from .modules.security.service import UserRole

            version_path = request.url.path.startswith(
                f"{settings.api_prefix}/algorithm-versions/"
            )
            version_build = version_path and request.url.path.endswith("/build")
            manual_start = (
                request.url.path.startswith(f"{settings.api_prefix}/algorithms/")
                and request.url.path.endswith("/start")
            )
            admin_path = request.url.path.startswith(admin_only_prefixes) or (
                version_path and not version_build
            ) or manual_start
            if admin_path and session.role is not UserRole.ADMIN:
                return JSONResponse(
                    status_code=403,
                    content={
                        "code": "FORBIDDEN",
                        "message": "需要管理员权限",
                        "request_id": request_id,
                    },
                )
            # 部署列表所有人可看（工作台选择部署实例），创建和启停需要开发者权限
            deployment_write = request.url.path.startswith(
                f"{settings.api_prefix}/deployments"
            ) and request.method != "GET"
            if (
                (request.url.path.startswith(developer_prefixes) or version_build
                 or deployment_write)
                and session.role not in {UserRole.ADMIN, UserRole.DEVELOPER}
            ):
                return JSONResponse(
                    status_code=403,
                    content={
                        "code": "FORBIDDEN",
                        "message": "需要算法开发者权限",
                        "request_id": request_id,
                    },
                )
            project_header = request.headers.get("X-Project-ID") or request.query_params.get(
                "project_id"
            )
            project_id = None
            if project_header:
                from uuid import UUID

                try:
                    project_id = UUID(project_header)
                except ValueError:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "code": "PROJECT_INVALID",
                            "message": "项目 ID 不合法",
                            "request_id": request_id,
                        },
                    )
            if project_id is None:
                project_id = container.projects.first_project_id(session.user_id)
            project_catalog = request.url.path == f"{settings.api_prefix}/projects"
            auth_path = request.url.path.startswith(f"{settings.api_prefix}/auth/")
            bypass_project_access = project_catalog or auth_path
            if bypass_project_access:
                request.state.project_id = project_id
            elif project_id is None:
                return JSONResponse(
                    status_code=403,
                    content={
                        "code": "PROJECT_REQUIRED",
                        "message": "当前用户尚未加入项目",
                        "request_id": request_id,
                    },
                )
            else:
                viewer_safe_post = request.url.path in {
                    f"{settings.api_prefix}/assets/download",
                    f"{settings.api_prefix}/tasks/results/archive",
                }
                try:
                    container.projects.require_access(
                        project_id,
                        session.user_id,
                        write=(
                            request.method in {"POST", "PUT", "PATCH", "DELETE"}
                            and not viewer_safe_post
                        ),
                    )
                except Exception as exc:
                    from .core.errors import ApplicationError

                    if isinstance(exc, ApplicationError):
                        return JSONResponse(
                            status_code=exc.status_code,
                            content={
                                "code": exc.code,
                                "message": exc.message,
                                "request_id": request_id,
                            },
                        )
                    raise
                request.state.project_id = project_id
        if request.method == "POST" and request.url.path in rate_limited_paths:
            # 未登录（登录接口）或免登录模式下大家都是同一个身份，再按来源 IP 区分，
            # 避免一个人用完配额后所有人都被限流。来源 IP 依赖 uvicorn 的 --proxy-headers。
            shared_identity = session is None or not settings.auth_enabled
            client_ip = request.client.host if request.client else "unknown"
            rate_key = f"{actor}@{client_ip}" if shared_identity else actor
            if not container.rate_limiter.allow(f"{rate_key}:{request.url.path}"):
                return JSONResponse(
                    status_code=429,
                    content={
                        "code": "RATE_LIMITED",
                        "message": "请求过于频繁，请稍后重试",
                        "request_id": request_id,
                    },
                )
        response = await call_next(request)
        METRICS.observe_http(
            request.method,
            response.status_code,
            time.perf_counter() - request_started,
        )
        response.headers["X-Request-ID"] = request_id
        if is_api and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            from .modules.security.service import AuditEvent

            container.audit_log.add(
                AuditEvent(
                    timestamp=datetime.now(UTC),
                    actor=actor,
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    request_id=request_id,
                )
            )
        return response

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        container = get_container()
        snapshot = container.operations.metrics()
        depths = None
        if container.database is not None:
            from .modules.tasks.infrastructure.persistent_queue import PostgresWorkClaimer

            depths = PostgresWorkClaimer(container.database).queue_depths()
        return Response(
            METRICS.render(snapshot, depths),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app


app = create_app()
