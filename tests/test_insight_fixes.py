import json

from project_service import metrics
from project_service.engine.knowledge_manager import KnowledgeManager
from project_service.engine.rag_coordinator import RAGCoordinator
from project_service.store.project_store import ProjectStore, SCHEMA_VERSION


def test_schema_version_is_4():
    assert SCHEMA_VERSION == 4


def test_always_include_migration_adds_column(tmp_path):
    store = ProjectStore(db_path=tmp_path / "a.db")
    import sqlite3
    conn = sqlite3.connect(str(store.db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_files)")}
    conn.close()
    assert "always_include" in cols


def test_create_and_toggle_always_include(tmp_path):
    store = ProjectStore(db_path=tmp_path / "b.db")
    pid = store.create_project({"name": "P"})["id"]
    kfile = store.create_knowledge_file({
        "project_id": pid,
        "name": "f",
        "original_name": "f.txt",
        "file_path": str(tmp_path / "f.txt"),
    })
    assert kfile["always_include"] == 0
    updated = store.update_knowledge_file(kfile["id"], {"always_include": 1})
    assert updated["always_include"] == 1
    always = store.list_always_include_files(pid)
    assert len(always) == 1
    assert always[0]["id"] == kfile["id"]


def test_list_projects_pagination(tmp_path):
    store = ProjectStore(db_path=tmp_path / "c.db")
    for i in range(5):
        store.create_project({"name": f"P{i}"})
    page1 = store.list_projects(limit=2, offset=0)
    page2 = store.list_projects(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    ids1 = {r["id"] for r in page1}
    ids2 = {r["id"] for r in page2}
    assert ids1.isdisjoint(ids2)


def test_rag_metrics_record(tmp_path):
    metrics.reset()
    metrics.record_rag_query(recalled=3, below_threshold=1)
    metrics.record_rag_query(recalled=0)
    snap = metrics.snapshot()
    assert snap["rag_query_total"] == 2
    assert snap["rag_avg_recall"] == 1.5
    assert snap["rag_zero_recall"] == 1
    assert snap["rag_below_threshold"] == 1
    metrics.reset()


def test_rag_coordinator_to_sources(tmp_path):
    store = ProjectStore(db_path=tmp_path / "d.db")
    rc = RAGCoordinator(store=store)
    pid = store.create_project({"name": "P"})["id"]
    store.create_knowledge_file({
        "project_id": pid,
        "name": "doc1",
        "original_name": "doc1.md",
        "file_path": str(tmp_path / "doc1.md"),
        "rag_doc_id": "rag-doc-1",
        "index_status": "INDEXED",
    })
    items = [{"doc_id": "rag-doc-1", "text": "hello world", "score": 0.9}]
    sources = rc._to_sources(items, pid)
    assert len(sources) == 1
    assert sources[0]["file_name"] == "doc1"
    assert sources[0]["snippet"] == "hello world"
    assert sources[0]["score"] == 0.9


def test_set_message_rag_sources(tmp_path):
    store = ProjectStore(db_path=tmp_path / "e.db")
    pid = store.create_project({"name": "P"})["id"]
    chat = store.create_chat({"project_id": pid, "title": "c"})
    msg = store.create_message({"chat_id": chat["id"], "role": "assistant", "content": "hi"})
    ok = store.set_message_rag_sources(msg["id"], json.dumps([{"file_name": "x"}]))
    assert ok is True
    got = store.get_message(msg["id"])
    assert json.loads(got["rag_sources"])[0]["file_name"] == "x"


def test_knowledge_manager_set_always_include(tmp_path):
    store = ProjectStore(db_path=tmp_path / "f.db")
    km = KnowledgeManager(store=store)
    pid = store.create_project({"name": "P"})["id"]
    kfile = store.create_knowledge_file({
        "project_id": pid,
        "name": "f",
        "original_name": "f.txt",
        "file_path": str(tmp_path / "f.txt"),
    })
    import asyncio
    result = asyncio.run(km.set_always_include(kfile["id"], True))
    assert result.always_include is True
