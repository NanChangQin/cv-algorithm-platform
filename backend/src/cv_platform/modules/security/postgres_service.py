from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from ...core.database import Database
from .service import AuditEvent, Session, User, UserRole


class PostgresAuthService:
    def __init__(
        self,
        database: Database,
        username: str,
        password: str,
        ttl_seconds: int,
    ) -> None:
        self._database = database
        self._ttl = ttl_seconds
        existing = self.find_by_username(username)
        if existing is None:
            self.create_user(username, password, UserRole.ADMIN)
        self._initial_admin_id = self.find_by_username(username).id

    @staticmethod
    def _from_user_row(row: dict[str, object]) -> User:
        return User(
            id=row["id"],
            username=str(row["username"]),
            role=UserRole(str(row["role"])),
            password_salt=bytes(row["password_salt"]),
            password_hash=bytes(row["password_hash"]),
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
        )

    def create_user(self, username: str, password: str, role: UserRole) -> User:
        normalized = username.strip().lower()
        if not normalized or len(normalized) > 128:
            raise ValueError("invalid username")
        if len(password) < 12:
            raise ValueError("password must contain at least 12 characters")
        salt = secrets.token_bytes(16)
        user = User(
            id=uuid4(),
            username=normalized,
            role=role,
            password_salt=salt,
            password_hash=self._derive(password, salt),
            enabled=True,
            created_at=datetime.now(UTC),
        )
        try:
            with self._database.connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO users (
                        id, username, role, password_salt, password_hash,
                        enabled, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user.id,
                        user.username,
                        user.role.value,
                        user.password_salt,
                        user.password_hash,
                        user.enabled,
                        user.created_at,
                        user.created_at,
                    ),
                )
        except UniqueViolation as exc:
            raise ValueError("username already exists") from exc
        return user

    def list_users(self) -> list[User]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM users ORDER BY created_at")
            return [self._from_user_row(row) for row in cursor.fetchall()]

    def find_by_username(self, username: str) -> User | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM users WHERE username = %s",
                (username.strip().lower(),),
            )
            row = cursor.fetchone()
            return self._from_user_row(row) if row else None

    def update_user(
        self,
        user_id: UUID,
        *,
        role: UserRole | None = None,
        enabled: bool | None = None,
    ) -> User:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE id = %s FOR UPDATE", (user_id,))
            row = cursor.fetchone()
            if row is None:
                raise ValueError("user does not exist")
            updated_role = role or UserRole(str(row["role"]))
            updated_enabled = bool(row["enabled"]) if enabled is None else enabled
            cursor.execute(
                """
                UPDATE users SET role = %s, enabled = %s, updated_at = now()
                WHERE id = %s RETURNING *
                """,
                (updated_role.value, updated_enabled, user_id),
            )
            updated = self._from_user_row(cursor.fetchone())
            if not updated.enabled:
                cursor.execute(
                    "UPDATE user_sessions SET revoked_at = now() "
                    "WHERE user_id = %s AND revoked_at IS NULL",
                    (user_id,),
                )
            return updated

    @property
    def initial_admin_id(self) -> UUID:
        return self._initial_admin_id

    def login(self, username: str, password: str) -> Session | None:
        user = self.find_by_username(username)
        if (
            user is None
            or not user.enabled
            or not hmac.compare_digest(
                user.password_hash,
                self._derive(password, user.password_salt),
            )
        ):
            return None
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        session = Session(
            token=token,
            user_id=user.id,
            username=user.username,
            role=user.role,
            expires_at=now + timedelta(seconds=self._ttl),
        )
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO user_sessions (id, user_id, token_hash, created_at, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (uuid4(), user.id, self._token_hash(token), now, session.expires_at),
            )
        return session

    def authenticate(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT s.user_id, s.expires_at, u.username, u.role, u.enabled
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = %s AND s.revoked_at IS NULL
                    AND s.expires_at > now() AND u.enabled = true
                """,
                (self._token_hash(token),),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return Session(
                token=token,
                user_id=row["user_id"],
                username=str(row["username"]),
                role=UserRole(str(row["role"])),
                expires_at=row["expires_at"],
            )

    def logout(self, token: str) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE user_sessions SET revoked_at = now() WHERE token_hash = %s",
                (self._token_hash(token),),
            )

    @staticmethod
    def _derive(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PostgresAuditLog:
    def __init__(self, database: Database) -> None:
        self._database = database

    def add(self, event: AuditEvent) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO audit_events (
                    project_id, actor_id, actor_label, action, method, path,
                    status_code, request_id, details, created_at
                ) VALUES (NULL, NULL, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event.actor,
                    f"{event.method} {event.path}",
                    event.method,
                    event.path,
                    event.status_code,
                    event.request_id,
                    Jsonb({}),
                    event.timestamp,
                ),
            )

    def list(self, limit: int = 200) -> list[AuditEvent]:
        bounded_limit = min(max(limit, 1), 1000)
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT actor_label, method, path, status_code, request_id, created_at
                FROM audit_events ORDER BY created_at DESC LIMIT %s
                """,
                (bounded_limit,),
            )
            return [
                AuditEvent(
                    timestamp=row["created_at"],
                    actor=str(row["actor_label"]),
                    method=str(row["method"]),
                    path=str(row["path"]),
                    status_code=int(row["status_code"]),
                    request_id=str(row["request_id"]),
                )
                for row in cursor.fetchall()
            ]
