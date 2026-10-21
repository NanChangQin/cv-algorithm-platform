from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from ...core.database import Database
from ...core.errors import ApplicationError
from ...core.project_context import DEFAULT_PROJECT_ID
from .service import Project, ProjectMembership, ProjectRole


class PostgresProjectService:
    def __init__(self, database: Database, admin_user_id: UUID) -> None:
        self._database = database
        now = datetime.now(UTC)
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO projects (
                    id, name, description, created_by, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    DEFAULT_PROJECT_ID,
                    "默认项目",
                    "PostgreSQL 持久化默认工作空间",
                    admin_user_id,
                    now,
                    now,
                ),
            )
            cursor.execute(
                """
                INSERT INTO project_memberships (project_id, user_id, role, joined_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (project_id, user_id) DO NOTHING
                """,
                (DEFAULT_PROJECT_ID, admin_user_id, ProjectRole.OWNER.value, now),
            )

    @staticmethod
    def _project(row: dict[str, object]) -> Project:
        return Project(
            id=row["id"],
            name=str(row["name"]),
            description=str(row["description"]),
            created_by=row["created_by"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _membership(row: dict[str, object]) -> ProjectMembership:
        return ProjectMembership(
            project_id=row["project_id"],
            user_id=row["user_id"],
            role=ProjectRole(
                str(row["membership_role"] if "membership_role" in row else row["role"])
            ),
            joined_at=row["joined_at"],
        )

    def create(self, name: str, description: str, user_id: UUID) -> Project:
        project = Project(uuid4(), name.strip(), description.strip(), user_id, datetime.now(UTC))
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO projects (id, name, description, created_by, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    project.id,
                    project.name,
                    project.description,
                    project.created_by,
                    project.created_at,
                    project.created_at,
                ),
            )
            cursor.execute(
                """
                INSERT INTO project_memberships (project_id, user_id, role, joined_at)
                VALUES (%s, %s, %s, %s)
                """,
                (project.id, user_id, ProjectRole.OWNER.value, project.created_at),
            )
        return project

    def list_for_user(self, user_id: UUID) -> list[tuple[Project, ProjectMembership]]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.*, m.user_id, m.role AS membership_role, m.joined_at,
                    m.project_id AS membership_project_id
                FROM projects p JOIN project_memberships m ON m.project_id = p.id
                WHERE m.user_id = %s ORDER BY p.created_at
                """,
                (user_id,),
            )
            return [
                (
                    self._project(row),
                    ProjectMembership(
                        project_id=row["membership_project_id"],
                        user_id=row["user_id"],
                        role=ProjectRole(str(row["membership_role"])),
                        joined_at=row["joined_at"],
                    ),
                )
                for row in cursor.fetchall()
            ]

    def first_project_id(self, user_id: UUID) -> UUID | None:
        projects = self.list_for_user(user_id)
        return projects[0][0].id if projects else None

    def add_member(
        self,
        project_id: UUID,
        actor_id: UUID,
        user_id: UUID,
        role: ProjectRole,
    ) -> ProjectMembership:
        self.require_access(project_id, actor_id, write=True, owner_only=True)
        membership = ProjectMembership(project_id, user_id, role, datetime.now(UTC))
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO project_memberships (project_id, user_id, role, joined_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (project_id, user_id)
                DO UPDATE SET role = EXCLUDED.role
                """,
                (project_id, user_id, role.value, membership.joined_at),
            )
        return membership

    def list_members(self, project_id: UUID, actor_id: UUID) -> list[ProjectMembership]:
        self.require_access(project_id, actor_id)
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id, user_id, role, joined_at
                FROM project_memberships WHERE project_id = %s ORDER BY joined_at
                """,
                (project_id,),
            )
            return [self._membership(row) for row in cursor.fetchall()]

    def require_access(
        self,
        project_id: UUID,
        user_id: UUID,
        *,
        write: bool = False,
        owner_only: bool = False,
    ) -> ProjectMembership:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT project_id, user_id, role, joined_at
                FROM project_memberships WHERE project_id = %s AND user_id = %s
                """,
                (project_id, user_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ApplicationError("PROJECT_FORBIDDEN", "无权访问该项目", 403)
        membership = self._membership(row)
        if owner_only and membership.role is not ProjectRole.OWNER:
            raise ApplicationError("PROJECT_FORBIDDEN", "需要项目所有者权限", 403)
        if write and membership.role is ProjectRole.VIEWER:
            raise ApplicationError("PROJECT_READ_ONLY", "项目访客只有读取权限", 403)
        return membership
