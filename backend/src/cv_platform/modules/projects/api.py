from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...core.errors import ApplicationError
from ...dependencies import get_container
from .service import ProjectRole

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    description: str
    role: ProjectRole
    created_at: datetime


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=500)


class AddMemberRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    role: ProjectRole


class MemberResponse(BaseModel):
    user_id: UUID
    username: str
    role: ProjectRole
    joined_at: datetime


def session_from(request: Request):
    session = getattr(request.state, "session", None)
    if session is None:
        raise ApplicationError("AUTH_REQUIRED", "请先登录", 401)
    return session


@router.get("", response_model=list[ProjectResponse])
def list_projects(request: Request, container=Depends(get_container)):
    session = session_from(request)
    return [
        ProjectResponse(
            id=project.id,
            name=project.name,
            description=project.description,
            role=membership.role,
            created_at=project.created_at,
        )
        for project, membership in container.projects.list_for_user(session.user_id)
    ]


@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(
    payload: CreateProjectRequest,
    request: Request,
    container=Depends(get_container),
):
    session = session_from(request)
    project = container.projects.create(payload.name, payload.description, session.user_id)
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        role=ProjectRole.OWNER,
        created_at=project.created_at,
    )


@router.get("/{project_id}/members", response_model=list[MemberResponse])
def list_members(project_id: UUID, request: Request, container=Depends(get_container)):
    session = session_from(request)
    memberships = container.projects.list_members(project_id, session.user_id)
    users = {user.id: user for user in container.auth.list_users()}
    return [
        MemberResponse(
            user_id=item.user_id,
            username=users[item.user_id].username,
            role=item.role,
            joined_at=item.joined_at,
        )
        for item in memberships
        if item.user_id in users
    ]


@router.post("/{project_id}/members", response_model=MemberResponse, status_code=201)
def add_member(
    project_id: UUID,
    payload: AddMemberRequest,
    request: Request,
    container=Depends(get_container),
):
    session = session_from(request)
    user = container.auth.find_by_username(payload.username)
    if user is None:
        raise ApplicationError("USER_NOT_FOUND", "用户不存在", 404)
    membership = container.projects.add_member(
        project_id, session.user_id, user.id, payload.role
    )
    return MemberResponse(
        user_id=user.id,
        username=user.username,
        role=membership.role,
        joined_at=membership.joined_at,
    )
