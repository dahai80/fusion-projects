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
    label TEXT NOT NULL DEFAULT 'auto',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS project_artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL UNIQUE,
    artifact_name TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    artifact_kind TEXT,
    content_summary TEXT,
    migrated_at TEXT NOT NULL,
    source_session_id TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    title TEXT,
    is_starred INTEGER NOT NULL DEFAULT 0,
    agent_id TEXT,
    fork_from_chat_id TEXT,
    fork_from_snapshot_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_snapshots (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    title TEXT,
    messages TEXT NOT NULL DEFAULT '[]',
    instruction_snapshot_id TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    agent_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    rag_sources TEXT,
    tool_calls TEXT,
    token_usage TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_folders (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_id TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES knowledge_folders(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS knowledge_files (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    folder_id TEXT,
    name TEXT NOT NULL,
    original_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    mime_type TEXT,
    rag_doc_id TEXT,
    index_status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (folder_id) REFERENCES knowledge_folders(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS chat_agent_bindings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    chat_id TEXT,
    agent_id TEXT,
    merge_mode TEXT NOT NULL DEFAULT 'AGENT_FIRST',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rag_queries (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    chat_id TEXT,
    query TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'AUTO',
    scope_folder_ids TEXT,
    top_k INTEGER NOT NULL DEFAULT 5,
    threshold REAL NOT NULL DEFAULT 0.65,
    results TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_instructions_project ON instructions(project_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_project ON instruction_snapshots(project_id);
CREATE INDEX IF NOT EXISTS idx_projects_archived ON projects(is_archived);
CREATE INDEX IF NOT EXISTS idx_projects_starred ON projects(is_starred);
CREATE INDEX IF NOT EXISTS idx_pa_project ON project_artifacts(project_id);
CREATE INDEX IF NOT EXISTS idx_pa_artifact ON project_artifacts(artifact_id);
CREATE INDEX IF NOT EXISTS idx_chats_project ON chats(project_id);
CREATE INDEX IF NOT EXISTS idx_chats_starred ON chats(is_starred);
CREATE INDEX IF NOT EXISTS idx_chat_snapshots_chat ON chat_snapshots(chat_id);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(chat_id, created_at);
CREATE INDEX IF NOT EXISTS idx_kf_project ON knowledge_folders(project_id);
CREATE INDEX IF NOT EXISTS idx_kf_parent ON knowledge_folders(parent_id);
CREATE INDEX IF NOT EXISTS idx_kfile_project ON knowledge_files(project_id);
CREATE INDEX IF NOT EXISTS idx_kfile_folder ON knowledge_files(folder_id);
CREATE INDEX IF NOT EXISTS idx_kfile_status ON knowledge_files(index_status);
CREATE INDEX IF NOT EXISTS idx_binding_project ON chat_agent_bindings(project_id);
CREATE INDEX IF NOT EXISTS idx_binding_chat ON chat_agent_bindings(chat_id);
CREATE INDEX IF NOT EXISTS idx_rag_project ON rag_queries(project_id);

CREATE TABLE IF NOT EXISTS temp_attachments (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    original_name TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    mime_type TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_temp_attachments_chat_id ON temp_attachments(chat_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    chat_id TEXT,
    action TEXT NOT NULL,
    agent_id TEXT,
    details TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_audit_log_project ON audit_log(project_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at);
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
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()
        logger.info("ProjectStore ready db=%s", self.db_path)

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()
            self._migrate_chat_project_id_nullable()
            self._migrate_snapshots()

    def _migrate_chat_project_id_nullable(self) -> None:
        with self._lock:
            cur = self._conn.execute("PRAGMA table_info(chats)")
            cols = cur.fetchall()
            for col in cols:
                if col[1] == "project_id" and col[3]:  # col[3] = notnull
                    logger.info("migrating chats.project_id to nullable")
                    self._conn.execute(
                        "CREATE TABLE chats_new ("
                        "id TEXT PRIMARY KEY, project_id TEXT, title TEXT, "
                        "is_starred INTEGER NOT NULL DEFAULT 0, agent_id TEXT, "
                        "fork_from_chat_id TEXT, fork_from_snapshot_id TEXT, "
                        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
                        "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE)"
                    )
                    self._conn.execute(
                        "INSERT INTO chats_new SELECT * FROM chats"
                    )
                    self._conn.execute("DROP TABLE chats")
                    self._conn.execute("ALTER TABLE chats_new RENAME TO chats")
                    self._conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_chats_project ON chats(project_id)"
                    )
                    self._conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_chats_starred ON chats(is_starred)"
                    )
                    self._conn.commit()
                    break

    def _migrate_snapshots(self) -> None:
        with self._lock:
            cur = self._conn.execute("PRAGMA table_info(instruction_snapshots)")
            for col in cur.fetchall():
                if col[1] == "label" and not col[3]:
                    logger.info("migrating instruction_snapshots.label to NOT NULL DEFAULT 'auto'")
                    self._conn.execute(
                        "UPDATE instruction_snapshots SET label='auto' WHERE label IS NULL"
                    )
                    self._conn.commit()
                    break
            cur = self._conn.execute("PRAGMA table_info(chat_snapshots)")
            col_names = {c[1] for c in cur.fetchall()}
            if "messages" not in col_names:
                logger.info("migrating chat_snapshots: add messages, instruction_snapshot_id")
                self._conn.execute(
                    "ALTER TABLE chat_snapshots ADD COLUMN messages TEXT NOT NULL DEFAULT '[]'"
                )
                self._conn.execute(
                    "ALTER TABLE chat_snapshots ADD COLUMN instruction_snapshot_id TEXT"
                )
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
        sql = (
            "SELECT p.*, "
            "(SELECT COUNT(*) FROM project_artifacts WHERE project_id=p.id) AS artifact_count "
            "FROM projects p WHERE p.id=?"
        )
        with self._cursor() as cur:
            cur.execute(sql, (project_id,))
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
            clauses.append("p.is_archived=0")
        if only_starred:
            clauses.append("p.is_starred=1")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT p.*, "
            "(SELECT COUNT(*) FROM project_artifacts WHERE project_id=p.id) AS artifact_count "
            "FROM projects p"
            + where
            + " ORDER BY p.is_starred DESC, p.updated_at DESC"
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

    def get_snapshot(self, snapshot_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM instruction_snapshots WHERE id=?", (snapshot_id,))
            r = cur.fetchone()
        return dict(r) if r else None

    def delete_snapshot(self, snapshot_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM instruction_snapshots WHERE id=?", (snapshot_id,))
            deleted = cur.rowcount
        if deleted:
            logger.info("deleted instruction_snapshot id=%s", snapshot_id)
        return deleted > 0

    def create_artifact_ref(self, data: dict) -> dict:
        aid = data.get("id") or uuid.uuid4().hex
        now = _now()
        row = {
            "id": aid,
            "project_id": data["project_id"],
            "artifact_id": data["artifact_id"],
            "artifact_name": data["artifact_name"],
            "artifact_type": data["artifact_type"],
            "artifact_kind": data.get("artifact_kind"),
            "content_summary": data.get("content_summary"),
            "migrated_at": now,
            "source_session_id": data.get("source_session_id"),
        }
        cols = ",".join(row.keys())
        placeholders = ",".join("?" for _ in row)
        with self._cursor() as cur:
            cur.execute(
                f"INSERT INTO project_artifacts ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
        logger.info("created artifact_ref id=%s artifact=%s project=%s", aid, data["artifact_id"], data["project_id"])
        return self.get_artifact_ref_by_id(aid)

    def get_artifact_ref_by_id(self, ref_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM project_artifacts WHERE id=?", (ref_id,))
            r = cur.fetchone()
        return dict(r) if r else None

    def get_artifact_ref(self, artifact_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM project_artifacts WHERE artifact_id=?", (artifact_id,))
            r = cur.fetchone()
        return dict(r) if r else None

    def list_artifact_refs(
        self,
        project_id: str,
        artifact_type: Optional[str] = None,
        artifact_kind: Optional[str] = None,
        search: Optional[str] = None,
    ) -> list[dict]:
        clauses = ["project_id=?"]
        params: list[Any] = [project_id]
        if artifact_type:
            clauses.append("artifact_type=?")
            params.append(artifact_type)
        if artifact_kind:
            clauses.append("artifact_kind=?")
            params.append(artifact_kind)
        if search:
            clauses.append("artifact_name LIKE ?")
            params.append(f"%{search}%")
        where = " AND ".join(clauses)
        sql = f"SELECT * FROM project_artifacts WHERE {where} ORDER BY migrated_at DESC"
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def remove_artifact_ref(self, artifact_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM project_artifacts WHERE artifact_id=?", (artifact_id,))
            deleted = cur.rowcount
        if deleted:
            logger.info("removed artifact_ref artifact=%s", artifact_id)
        return deleted > 0

    def count_artifact_refs(self, project_id: str) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM project_artifacts WHERE project_id=?", (project_id,))
            return cur.fetchone()[0]

    # ── Chat CRUD ──

    def create_chat(self, data: dict) -> dict:
        cid = data.get("id") or uuid.uuid4().hex
        now = _now()
        row = {
            "id": cid,
            "project_id": data["project_id"],
            "title": data.get("title"),
            "is_starred": 0,
            "agent_id": data.get("agent_id"),
            "fork_from_chat_id": data.get("fork_from_chat_id"),
            "fork_from_snapshot_id": data.get("fork_from_snapshot_id"),
            "created_at": now,
            "updated_at": now,
        }
        cols = ",".join(row.keys())
        placeholders = ",".join("?" for _ in row)
        with self._cursor() as cur:
            cur.execute(
                f"INSERT INTO chats ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
        logger.info("created chat id=%s project=%s", cid, data["project_id"])
        return self.get_chat(cid)

    def get_chat(self, chat_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM chats WHERE id=?", (chat_id,))
            r = cur.fetchone()
        return dict(r) if r else None

    def list_chats(self, project_id: str, only_starred: bool = False) -> list[dict]:
        clauses = ["project_id=?"]
        params: list[Any] = [project_id]
        if only_starred:
            clauses.append("is_starred=1")
        where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM chats{where} ORDER BY is_starred DESC, updated_at DESC"
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def update_chat(self, chat_id: str, fields: dict) -> Optional[dict]:
        allowed = {"title", "is_starred", "agent_id", "project_id"}
        clean = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not clean:
            return self.get_chat(chat_id)
        clean["updated_at"] = _now()
        sets = ",".join(f"{k}=?" for k in clean)
        vals = list(clean.values()) + [chat_id]
        with self._cursor() as cur:
            cur.execute(f"UPDATE chats SET {sets} WHERE id=?", vals)
            if cur.rowcount == 0:
                logger.warning("update_chat no row chat=%s", chat_id)
                return None
        logger.info("updated chat id=%s fields=%s", chat_id, list(clean.keys()))
        return self.get_chat(chat_id)

    def delete_chat(self, chat_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM chats WHERE id=?", (chat_id,))
            deleted = cur.rowcount
        if deleted:
            logger.info("deleted chat id=%s", chat_id)
        return deleted > 0

    def detach_chat(self, chat_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE chats SET project_id=NULL, updated_at=? WHERE id=?",
                (_now(), chat_id),
            )
            if cur.rowcount == 0:
                return None
        logger.info("detached chat id=%s", chat_id)
        return self.get_chat(chat_id)

    # ── Chat Snapshot ──

    def create_chat_snapshot(self, data: dict) -> dict:
        sid = data.get("id") or uuid.uuid4().hex
        now = _now()
        row = {
            "id": sid,
            "chat_id": data["chat_id"],
            "title": data.get("title"),
            "message_count": data.get("message_count", 0),
            "agent_id": data.get("agent_id"),
            "created_at": now,
        }
        cols = ",".join(row.keys())
        placeholders = ",".join("?" for _ in row)
        with self._cursor() as cur:
            cur.execute(
                f"INSERT INTO chat_snapshots ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
        logger.info("created chat_snapshot id=%s chat=%s", sid, data["chat_id"])
        return row

    def list_chat_snapshots(self, chat_id: str) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM chat_snapshots WHERE chat_id=? ORDER BY created_at DESC",
                (chat_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_chat_snapshot(self, snapshot_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM chat_snapshots WHERE id=?", (snapshot_id,))
            r = cur.fetchone()
        return dict(r) if r else None

    def delete_chat_snapshot(self, snapshot_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM chat_snapshots WHERE id=?", (snapshot_id,))
            deleted = cur.rowcount
        if deleted:
            logger.info("deleted chat_snapshot id=%s", snapshot_id)
        return deleted > 0

    # ── Message CRUD ──

    def create_message(self, data: dict) -> dict:
        mid = data.get("id") or uuid.uuid4().hex
        now = _now()
        row = {
            "id": mid,
            "chat_id": data["chat_id"],
            "role": data["role"],
            "content": data["content"],
            "rag_sources": data.get("rag_sources"),
            "tool_calls": data.get("tool_calls"),
            "token_usage": data.get("token_usage"),
            "created_at": now,
        }
        cols = ",".join(row.keys())
        placeholders = ",".join("?" for _ in row)
        with self._cursor() as cur:
            cur.execute(
                f"INSERT INTO messages ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
        logger.info("created message id=%s chat=%s role=%s", mid, data["chat_id"], data["role"])
        return self.get_message(mid)

    def get_message(self, message_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM messages WHERE id=?", (message_id,))
            r = cur.fetchone()
        return dict(r) if r else None

    def list_messages(self, chat_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM messages WHERE chat_id=? ORDER BY created_at ASC LIMIT ? OFFSET ?",
                (chat_id, limit, offset),
            )
            return [dict(r) for r in cur.fetchall()]

    def count_messages(self, chat_id: str) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM messages WHERE chat_id=?", (chat_id,))
            return cur.fetchone()[0]

    def dump_chat_messages(self, chat_id: str) -> str:
        import json
        with self._cursor() as cur:
            cur.execute(
                "SELECT id, role, content, created_at FROM messages WHERE chat_id=? ORDER BY created_at ASC",
                (chat_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        return json.dumps(rows, ensure_ascii=False)

    def delete_message(self, message_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM messages WHERE id=?", (message_id,))
            deleted = cur.rowcount
        if deleted:
            logger.info("deleted message id=%s", message_id)
        return deleted > 0

    # ── Knowledge Folder CRUD ──

    def create_folder(self, data: dict) -> dict:
        fid = data.get("id") or uuid.uuid4().hex
        now = _now()
        row = {
            "id": fid,
            "project_id": data["project_id"],
            "name": data["name"],
            "parent_id": data.get("parent_id"),
            "sort_order": data.get("sort_order", 0),
            "created_at": now,
        }
        cols = ",".join(row.keys())
        placeholders = ",".join("?" for _ in row)
        with self._cursor() as cur:
            cur.execute(
                f"INSERT INTO knowledge_folders ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
        logger.info("created folder id=%s project=%s name=%s", fid, data["project_id"], data["name"])
        return self.get_folder(fid)

    def get_folder(self, folder_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM knowledge_folders WHERE id=?", (folder_id,))
            r = cur.fetchone()
        return dict(r) if r else None

    def list_folders(self, project_id: str, parent_id: Optional[str] = None) -> list[dict]:
        if parent_id is not None:
            sql = "SELECT * FROM knowledge_folders WHERE project_id=? AND parent_id=? ORDER BY sort_order, name"
            params: list[Any] = [project_id, parent_id]
        else:
            sql = "SELECT * FROM knowledge_folders WHERE project_id=? ORDER BY sort_order, name"
            params = [project_id]
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def update_folder(self, folder_id: str, fields: dict) -> Optional[dict]:
        allowed = {"name", "parent_id", "sort_order"}
        clean = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not clean:
            return self.get_folder(folder_id)
        sets = ",".join(f"{k}=?" for k in clean)
        vals = list(clean.values()) + [folder_id]
        with self._cursor() as cur:
            cur.execute(f"UPDATE knowledge_folders SET {sets} WHERE id=?", vals)
            if cur.rowcount == 0:
                logger.warning("update_folder no row folder=%s", folder_id)
                return None
        logger.info("updated folder id=%s fields=%s", folder_id, list(clean.keys()))
        return self.get_folder(folder_id)

    def delete_folder(self, folder_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM knowledge_folders WHERE id=?", (folder_id,))
            deleted = cur.rowcount
        if deleted:
            logger.info("deleted folder id=%s", folder_id)
        return deleted > 0

    # ── Knowledge File CRUD ──

    def create_knowledge_file(self, data: dict) -> dict:
        fid = data.get("id") or uuid.uuid4().hex
        now = _now()
        row = {
            "id": fid,
            "project_id": data["project_id"],
            "folder_id": data.get("folder_id"),
            "name": data["name"],
            "original_name": data["original_name"],
            "file_path": data["file_path"],
            "file_size": data.get("file_size", 0),
            "mime_type": data.get("mime_type"),
            "rag_doc_id": data.get("rag_doc_id"),
            "index_status": data.get("index_status", "PENDING"),
            "created_at": now,
            "updated_at": now,
        }
        cols = ",".join(row.keys())
        placeholders = ",".join("?" for _ in row)
        with self._cursor() as cur:
            cur.execute(
                f"INSERT INTO knowledge_files ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
        logger.info("created knowledge_file id=%s project=%s name=%s", fid, data["project_id"], data["name"])
        return self.get_knowledge_file(fid)

    def get_knowledge_file(self, file_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM knowledge_files WHERE id=?", (file_id,))
            r = cur.fetchone()
        return dict(r) if r else None

    def list_knowledge_files(self, project_id: str, folder_id: Optional[str] = None) -> list[dict]:
        if folder_id is not None:
            sql = "SELECT * FROM knowledge_files WHERE project_id=? AND folder_id=? ORDER BY name"
            params: list[Any] = [project_id, folder_id]
        else:
            sql = "SELECT * FROM knowledge_files WHERE project_id=? ORDER BY name"
            params = [project_id]
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def update_knowledge_file(self, file_id: str, fields: dict) -> Optional[dict]:
        allowed = {"folder_id", "name", "index_status", "rag_doc_id"}
        clean = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not clean:
            return self.get_knowledge_file(file_id)
        clean["updated_at"] = _now()
        sets = ",".join(f"{k}=?" for k in clean)
        vals = list(clean.values()) + [file_id]
        with self._cursor() as cur:
            cur.execute(f"UPDATE knowledge_files SET {sets} WHERE id=?", vals)
            if cur.rowcount == 0:
                logger.warning("update_knowledge_file no row file=%s", file_id)
                return None
        logger.info("updated knowledge_file id=%s fields=%s", file_id, list(clean.keys()))
        return self.get_knowledge_file(file_id)

    def delete_knowledge_file(self, file_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM knowledge_files WHERE id=?", (file_id,))
            deleted = cur.rowcount
        if deleted:
            logger.info("deleted knowledge_file id=%s", file_id)
        return deleted > 0

    # ── Chat Agent Binding CRUD ──

    def create_binding(self, data: dict) -> dict:
        bid = data.get("id") or uuid.uuid4().hex
        now = _now()
        row = {
            "id": bid,
            "project_id": data["project_id"],
            "chat_id": data.get("chat_id"),
            "agent_id": data.get("agent_id"),
            "merge_mode": data.get("merge_mode", "AGENT_FIRST"),
            "created_at": now,
            "updated_at": now,
        }
        cols = ",".join(row.keys())
        placeholders = ",".join("?" for _ in row)
        with self._cursor() as cur:
            cur.execute(
                f"INSERT INTO chat_agent_bindings ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
        logger.info("created binding id=%s project=%s agent=%s", bid, data["project_id"], data.get("agent_id"))
        return self.get_binding(bid)

    def get_binding(self, binding_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM chat_agent_bindings WHERE id=?", (binding_id,))
            r = cur.fetchone()
        return dict(r) if r else None

    def get_binding_by_chat(self, chat_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM chat_agent_bindings WHERE chat_id=?", (chat_id,))
            r = cur.fetchone()
        return dict(r) if r else None

    def get_binding_by_project(self, project_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM chat_agent_bindings WHERE project_id=? AND chat_id IS NULL",
                (project_id,),
            )
            r = cur.fetchone()
        return dict(r) if r else None

    def update_binding(self, binding_id: str, fields: dict) -> Optional[dict]:
        allowed = {"agent_id", "merge_mode"}
        clean = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not clean:
            return self.get_binding(binding_id)
        clean["updated_at"] = _now()
        sets = ",".join(f"{k}=?" for k in clean)
        vals = list(clean.values()) + [binding_id]
        with self._cursor() as cur:
            cur.execute(f"UPDATE chat_agent_bindings SET {sets} WHERE id=?", vals)
            if cur.rowcount == 0:
                logger.warning("update_binding no row binding=%s", binding_id)
                return None
        logger.info("updated binding id=%s fields=%s", binding_id, list(clean.keys()))
        return self.get_binding(binding_id)

    def delete_binding(self, binding_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM chat_agent_bindings WHERE id=?", (binding_id,))
            deleted = cur.rowcount
        if deleted:
            logger.info("deleted binding id=%s", binding_id)
        return deleted > 0

    # ── RAG Query ──

    def create_rag_query(self, data: dict) -> dict:
        qid = data.get("id") or uuid.uuid4().hex
        now = _now()
        row = {
            "id": qid,
            "project_id": data["project_id"],
            "chat_id": data.get("chat_id"),
            "query": data["query"],
            "mode": data.get("mode", "AUTO"),
            "scope_folder_ids": data.get("scope_folder_ids"),
            "top_k": data.get("top_k", 5),
            "threshold": data.get("threshold", 0.65),
            "results": data.get("results"),
            "created_at": now,
        }
        cols = ",".join(row.keys())
        placeholders = ",".join("?" for _ in row)
        with self._cursor() as cur:
            cur.execute(
                f"INSERT INTO rag_queries ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
        logger.info("created rag_query id=%s project=%s mode=%s", qid, data["project_id"], data.get("mode", "AUTO"))
        return row

    # ── Temp Attachment CRUD ──

    def create_temp_attachment(self, data: dict) -> dict:
        aid = data.get("id") or uuid.uuid4().hex
        now = _now()
        row = {
            "id": aid,
            "chat_id": data["chat_id"],
            "file_path": data["file_path"],
            "original_name": data["original_name"],
            "file_size": data.get("file_size", 0),
            "mime_type": data.get("mime_type"),
            "created_at": now,
        }
        cols = ",".join(row.keys())
        placeholders = ",".join("?" for _ in row)
        with self._cursor() as cur:
            cur.execute(
                f"INSERT INTO temp_attachments ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
        logger.info("created temp_attachment id=%s chat=%s name=%s", aid, data["chat_id"], data["original_name"])
        return row

    def list_temp_attachments(self, chat_id: str) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM temp_attachments WHERE chat_id=? ORDER BY created_at ASC",
                (chat_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_temp_attachment(self, attachment_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM temp_attachments WHERE id=?", (attachment_id,))
            r = cur.fetchone()
        return dict(r) if r else None

    def delete_temp_attachment(self, attachment_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM temp_attachments WHERE id=?", (attachment_id,))
            deleted = cur.rowcount
        if deleted:
            logger.info("deleted temp_attachment id=%s", attachment_id)
        return deleted > 0

    # ── Audit Log ──

    def create_audit_log(self, data: dict) -> dict:
        aid = data.get("id") or uuid.uuid4().hex
        now = _now()
        row = {
            "id": aid,
            "project_id": data["project_id"],
            "chat_id": data.get("chat_id"),
            "action": data["action"],
            "agent_id": data.get("agent_id"),
            "details": data.get("details"),
            "created_at": now,
        }
        cols = ",".join(row.keys())
        placeholders = ",".join("?" for _ in row)
        with self._cursor() as cur:
            cur.execute(
                f"INSERT INTO audit_log ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
        logger.info("audit_log created id=%s project=%s action=%s", aid, data["project_id"], data["action"])
        return row

    def list_audit_log(
        self,
        project_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM audit_log WHERE project_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (project_id, limit, offset),
            )
            return [dict(r) for r in cur.fetchall()]

    # ── Cowork tasks ──

    def create_cowork_task(self, data: dict) -> dict:
        with self._cursor() as cur:
            row = dict(data)
            cols = ", ".join(row.keys())
            placeholders = ", ".join(["?"] * len(row))
            cur.execute(
                f"INSERT INTO cowork_tasks ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
            logger.info("cowork_task created id=%s action=%s", data["id"], data["action"])
            return row

    def get_cowork_task(self, task_id: str) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM cowork_tasks WHERE id=?", (task_id,))
            r = cur.fetchone()
            return dict(r) if r else None

    def update_cowork_task(self, data: dict) -> dict:
        with self._cursor() as cur:
            sets = []
            vals = []
            for k, v in data.items():
                if k != "id":
                    sets.append(f"{k}=?")
                    vals.append(v)
            vals.append(data["id"])
            cur.execute(
                f"UPDATE cowork_tasks SET {', '.join(sets)} WHERE id=?",
                vals,
            )
            cur.execute("SELECT * FROM cowork_tasks WHERE id=?", (data["id"],))
            return dict(cur.fetchone())
