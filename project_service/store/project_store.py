import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from project_service import config

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_archived INTEGER NOT NULL DEFAULT 0,
    is_starred INTEGER NOT NULL DEFAULT 0,
    default_agent_id TEXT,
    prompt_merge_mode TEXT NOT NULL DEFAULT 'AGENT_FIRST',
    rag_mode TEXT NOT NULL DEFAULT 'AUTO',
    rag_top_k INTEGER NOT NULL DEFAULT 5,
    rag_threshold REAL NOT NULL DEFAULT 0.65,
    kb_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instructions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS instruction_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    content TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_instructions_project ON instructions(project_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_project ON instruction_snapshots(project_id);
CREATE INDEX IF NOT EXISTS idx_projects_archived ON projects(is_archived);
CREATE INDEX IF NOT EXISTS idx_projects_starred ON projects(is_starred);
"""

ALLOWED_UPDATE_FIELDS = {
    "name",
    "description",
    "default_agent_id",
    "prompt_merge_mode",
    "rag_mode",
    "rag_top_k",
    "rag_threshold",
    "kb_id",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectStore:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else config.DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()
        logger.info("ProjectStore ready db=%s", self.db_path)

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                logger.exception("db transaction failed, rolled back")
                raise
            finally:
                cur.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
            logger.info("ProjectStore closed")

    def project_exists(self, project_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("SELECT 1 FROM projects WHERE id=?", (project_id,))
            return cur.fetchone() is not None

    def create_project(self, data: dict) -> dict:
        pid = data.get("id") or uuid.uuid4().hex
        now = _now()
        row = {
            "id": pid,
            "name": data["name"],
            "description": data.get("description", ""),
            "is_archived": 0,
            "is_starred": 0,
            "default_agent_id": data.get("default_agent_id"),
            "prompt_merge_mode": data.get("prompt_merge_mode", config.DEFAULT_PROMPT_MERGE),
            "rag_mode": data.get("rag_mode", config.DEFAULT_RAG_MODE),
            "rag_top_k": data.get("rag_top_k", config.DEFAULT_RAG_TOP_K),
            "rag_threshold": data.get("rag_threshold", config.DEFAULT_RAG_THRESHOLD),
            "kb_id": data.get("kb_id"),
            "created_at": now,
            "updated_at": now,
        }
        cols = ",".join(row.keys())
        placeholders = ",".join("?" for _ in row)
        with self._cursor() as cur:
            cur.execute(
                f"INSERT INTO projects ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
        logger.info("created project id=%s name=%s", pid, row["name"])
        return self.get_project(pid)

    def get_project(self, project_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM projects WHERE id=?", (project_id,))
            r = cur.fetchone()
        return dict(r) if r else None

    def list_projects(
        self,
        include_archived: bool = False,
        only_starred: bool = False,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_archived:
            clauses.append("is_archived=0")
        if only_starred:
            clauses.append("is_starred=1")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT * FROM projects"
            + where
            + " ORDER BY is_starred DESC, updated_at DESC"
        )
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def update_project(self, project_id: str, fields: dict) -> Optional[dict]:
        clean = {k: v for k, v in fields.items() if k in ALLOWED_UPDATE_FIELDS and v is not None}
        if not clean:
            return self.get_project(project_id)
        clean["updated_at"] = _now()
        sets = ",".join(f"{k}=?" for k in clean)
        vals = list(clean.values()) + [project_id]
        with self._cursor() as cur:
            cur.execute(f"UPDATE projects SET {sets} WHERE id=?", vals)
            if cur.rowcount == 0:
                logger.warning("update_project no row project=%s", project_id)
                return None
        logger.info("updated project id=%s fields=%s", project_id, list(clean.keys()))
        return self.get_project(project_id)

    def set_archived(self, project_id: str, archived: bool) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE projects SET is_archived=?, updated_at=? WHERE id=?",
                (1 if archived else 0, _now(), project_id),
            )
            if cur.rowcount == 0:
                return None
        logger.info("set_archived project=%s archived=%s", project_id, archived)
        return self.get_project(project_id)

    def set_starred(self, project_id: str, starred: bool) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE projects SET is_starred=?, updated_at=? WHERE id=?",
                (1 if starred else 0, _now(), project_id),
            )
            if cur.rowcount == 0:
                return None
        logger.info("set_starred project=%s starred=%s", project_id, starred)
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM projects WHERE id=?", (project_id,))
            deleted = cur.rowcount
        if deleted:
            logger.info("deleted project id=%s", project_id)
        return deleted > 0

    def get_instructions(self, project_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM instructions WHERE project_id=? ORDER BY updated_at DESC LIMIT 1",
                (project_id,),
            )
            r = cur.fetchone()
        return dict(r) if r else None

    def save_instructions(self, project_id: str, content: str) -> dict:
        existing = self.get_instructions(project_id)
        now = _now()
        if existing:
            with self._cursor() as cur:
                cur.execute(
                    "UPDATE instructions SET content=?, updated_at=? WHERE id=?",
                    (content, now, existing["id"]),
                )
            iid = existing["id"]
        else:
            iid = uuid.uuid4().hex
            with self._cursor() as cur:
                cur.execute(
                    "INSERT INTO instructions (id, project_id, content, created_at, updated_at) VALUES (?,?,?,?,?)",
                    (iid, project_id, content, now, now),
                )
        logger.info("saved instructions project=%s len=%d", project_id, len(content))
        return self.get_instructions(project_id)

    def clear_instructions(self, project_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM instructions WHERE project_id=?", (project_id,))
            deleted = cur.rowcount
        if deleted:
            logger.info("cleared instructions project=%s", project_id)
        return deleted > 0

    def snapshot_instruction(
        self,
        project_id: str,
        content: str,
        label: Optional[str] = None,
    ) -> dict:
        sid = uuid.uuid4().hex
        now = _now()
        row = {
            "id": sid,
            "project_id": project_id,
            "content": content,
            "label": label,
            "created_at": now,
        }
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO instruction_snapshots (id, project_id, content, label, created_at) VALUES (?,?,?,?,?)",
                (sid, project_id, content, label, now),
            )
        logger.info("snapshotted instruction project=%s label=%s", project_id, label)
        return row

    def list_snapshots(self, project_id: str) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM instruction_snapshots WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            )
            return [dict(r) for r in cur.fetchall()]
