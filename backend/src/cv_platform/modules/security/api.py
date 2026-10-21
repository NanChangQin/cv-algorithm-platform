from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ...core.config import get_settings
from ...core.errors import ApplicationError
from ...dependencies import get_container
from .service import UserRole

router = APIRouter(tags=["security"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=256)


class SessionResponse(BaseModel):
    user_id: UUID
    username: str
    role: str
    expires_at: datetime
    default_project_id: UUID | None = None
    authentication_enabled: bool


class CreateUserRequest(BaseModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{3,128}$")
    password: str = Field(min_length=12, max_length=256)
    role: UserRole


class UserResponse(BaseModel):
    id: UUID
    username: str
    role: UserRole
    enabled: bool
    created_at: datetime


class UpdateUserRequest(BaseModel):
    role: UserRole | None = None
    enabled: bool | None = None


@router.post("/auth/login", response_model=SessionResponse)
def login(
    payload: LoginRequest,
    response: Response,
    container=Depends(get_container),
) -> SessionResponse:
    session = container.auth.login(payload.username, payload.password)
    if session is None:
        raise ApplicationError("AUTH_INVALID", "用户名或密码错误", 401)
    response.set_cookie(
        "cv_session",
        session.token,
        httponly=True,
        samesite="strict",
        secure=get_settings().secure_cookies,
        max_age=int((session.expires_at - datetime.now(session.expires_at.tzinfo)).total_seconds()),
        path="/",
    )
    return SessionResponse(
        user_id=session.user_id,
        username=session.username,
        role=session.role.value,
        expires_at=session.expires_at,
        default_project_id=container.projects.first_project_id(session.user_id),
        authentication_enabled=get_settings().auth_enabled,
    )


@router.get("/auth/me", response_model=SessionResponse)
def me(request: Request, container=Depends(get_container)) -> SessionResponse:
    session = getattr(request.state, "session", None)
    if session is None:
        raise ApplicationError("AUTH_REQUIRED", "请先登录", 401)
    return SessionResponse(
        user_id=session.user_id,
        username=session.username,
        role=session.role.value,
        expires_at=session.expires_at,
        default_project_id=container.projects.first_project_id(session.user_id),
        authentication_enabled=get_settings().auth_enabled,
    )


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, container=Depends(get_container)) -> None:
    token = getattr(request.state, "auth_token", None)
    if token:
        container.auth.logout(token)
    response.delete_cookie("cv_session", path="/")


@router.get("/system/audit-logs")
def audit_logs(limit: int = 200, container=Depends(get_container)) -> list[dict[str, object]]:
    return [
        {
            "timestamp": event.timestamp,
            "actor": event.actor,
            "method": event.method,
            "path": event.path,
            "status_code": event.status_code,
            "request_id": event.request_id,
        }
        for event in container.audit_log.list(limit)
    ]


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(payload: CreateUserRequest, container=Depends(get_container)) -> UserResponse:
    try:
        user = container.auth.create_user(payload.username, payload.password, payload.role)
    except ValueError as exc:
        raise ApplicationError("USER_INVALID", str(exc), 409) from exc
    return UserResponse.model_validate(user, from_attributes=True)


@router.get("/users", response_model=list[UserResponse])
def list_users(container=Depends(get_container)) -> list[UserResponse]:
    return [
        UserResponse.model_validate(user, from_attributes=True)
        for user in container.auth.list_users()
    ]


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: UUID,
    payload: UpdateUserRequest,
    request: Request,
    container=Depends(get_container),
) -> UserResponse:
    session = getattr(request.state, "session", None)
    if session and session.user_id == user_id and payload.enabled is False:
        raise ApplicationError("USER_INVALID", "不能停用当前登录账号", 409)
    try:
        user = container.auth.update_user(user_id, **payload.model_dump())
    except ValueError as exc:
        raise ApplicationError("USER_NOT_FOUND", "用户不存在", 404) from exc
    return UserResponse.model_validate(user, from_attributes=True)
