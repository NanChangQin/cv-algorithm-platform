"""Durable, project-scoped human review of inference samples."""

from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from ...core.errors import ApplicationError


class ReviewStore:
    def __init__(self, database=None):
        self.database = database
        self.lock = RLock()
        self.items = {}

    def list(self, project):
        if self.database:
            with self.database.connect() as conn:
                return [
                    r["state"]
                    for r in conn.execute(
                        "SELECT state FROM sample_reviews WHERE project_id=%s "
                        "ORDER BY updated_at DESC",
                        (project,),
                    ).fetchall()
                ]
        with self.lock:
            return sorted(
                [deepcopy(v) for v in self.items.values() if v["project_id"] == str(project)],
                key=lambda v: v["updated_at"],
                reverse=True,
            )

    def get(self, key, project):
        if self.database:
            with self.database.connect() as conn:
                row = conn.execute(
                    "SELECT state FROM sample_reviews WHERE id=%s AND project_id=%s", (key, project)
                ).fetchone()
                item = row["state"] if row else None
        else:
            item = deepcopy(self.items.get(str(key)))
        if not item or item["project_id"] != str(project):
            raise ApplicationError("REVIEW_NOT_FOUND", "审核样本不存在", 404)
        return item

    def create(self, project, task_id, data):
        with self.lock:
            existing = next((v for v in self.list(project) if v["task_id"] == str(task_id)), None)
            if existing:
                return existing
            now = datetime.now(UTC).isoformat()
            item = {
                "id": str(uuid4()),
                "project_id": str(project),
                "task_id": str(task_id),
                "version": 1,
                "status": "draft",
                "created_at": now,
                "updated_at": now,
                "publications": [],
                **data,
            }
            if self.database:
                with self.database.connect() as conn:
                    conn.execute(
                        """INSERT INTO sample_reviews(id,project_id,task_id,state,updated_at)
                                    VALUES(%s,%s,%s,%s,%s)
                                    ON CONFLICT(project_id,task_id) DO NOTHING""",
                        (UUID(item["id"]), project, task_id, Jsonb(item), now),
                    )
                    return conn.execute(
                        "SELECT state FROM sample_reviews WHERE project_id=%s AND task_id=%s",
                        (project, task_id),
                    ).fetchone()["state"]
            self.items[item["id"]] = deepcopy(item)
            return item

    def update(self, key, project, version, changes):
        with self.lock:
            item = self.get(key, project)
            if item["version"] != version:
                raise ApplicationError("REVIEW_CONFLICT", "样本已被其他操作修改，请刷新后重试", 409)
            item.update(changes, version=version + 1, updated_at=datetime.now(UTC).isoformat())
            if self.database:
                with self.database.connect() as conn:
                    row = conn.execute(
                        """UPDATE sample_reviews SET state=%s, updated_at=%s
                      WHERE id=%s AND project_id=%s AND (state->>'version')::int=%s RETURNING id""",
                        (Jsonb(item), item["updated_at"], key, project, version),
                    ).fetchone()
                    if not row:
                        raise ApplicationError("REVIEW_CONFLICT", "样本已被修改，请刷新后重试", 409)
            else:
                self.items[str(key)] = deepcopy(item)
            return item
