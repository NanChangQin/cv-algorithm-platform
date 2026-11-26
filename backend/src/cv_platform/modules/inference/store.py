from copy import deepcopy
from threading import RLock
from uuid import UUID

from psycopg.types.json import Jsonb

from ...core.errors import ApplicationError


class SessionStore:
    def __init__(self, database=None):
        self.database = database
        self.lock = RLock()
        self.items = {}
        self.results = {}

    def save(self, state):
        if self.database is None:
            with self.lock:
                self.items[state["id"]] = deepcopy(state)
        else:
            with self.database.connect() as conn:
                conn.execute(
                    """INSERT INTO inference_sessions(id,project_id,state,created_at)
                    VALUES(%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET state=excluded.state""",
                    (
                        UUID(state["id"]),
                        UUID(state["project_id"]),
                        Jsonb(state),
                        state["created_at"],
                    ),
                )
        return deepcopy(state)

    def get(self, session_id, project_id=None):
        if self.database is None:
            with self.lock:
                state = deepcopy(self.items.get(str(session_id)))
        else:
            with self.database.connect() as conn:
                row = conn.execute(
                    "SELECT state FROM inference_sessions WHERE id=%s", (UUID(str(session_id)),)
                ).fetchone()
                state = row["state"] if row else None
        if not state or (project_id and state["project_id"] != str(project_id)):
            raise ApplicationError("INFERENCE_NOT_FOUND", "推理会话不存在", 404)
        return state

    def list(self, project_id=None, limit=100, offset=0):
        if self.database is None:
            with self.lock:
                rows = [
                    deepcopy(s)
                    for s in self.items.values()
                    if not project_id or s["project_id"] == str(project_id)
                ]
            return sorted(rows, key=lambda s: s["created_at"], reverse=True)[
                offset : offset + limit
            ]
        with self.database.connect() as conn:
            return [
                r["state"]
                for r in conn.execute(
                    """SELECT state FROM inference_sessions
                WHERE (%s::uuid IS NULL OR project_id=%s) ORDER BY created_at DESC LIMIT %s OFFSET %s""",
                    (project_id, project_id, limit, offset),
                ).fetchall()
            ]

    def frame(self, session_id, data):
        if self.database is None:
            with self.lock:
                self.results.setdefault(str(session_id), {})[data["index"]] = deepcopy(data)
        else:
            with self.database.connect() as conn:
                conn.execute(
                    """INSERT INTO inference_frames(session_id,frame_index,data) VALUES(%s,%s,%s)
                    ON CONFLICT(session_id,frame_index) DO UPDATE SET data=excluded.data""",
                    (UUID(str(session_id)), data["index"], Jsonb(data)),
                )

    def frames(self, session_id, limit=50, offset=0):
        if self.database is None:
            with self.lock:
                values = self.results.get(str(session_id), {})
                return [deepcopy(values[k]) for k in sorted(values)[offset : offset + limit]]
        with self.database.connect() as conn:
            return [
                r["data"]
                for r in conn.execute(
                    """SELECT data FROM inference_frames
                WHERE session_id=%s ORDER BY frame_index LIMIT %s OFFSET %s""",
                    (UUID(str(session_id)), limit, offset),
                ).fetchall()
            ]
