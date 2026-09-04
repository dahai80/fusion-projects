import json

import pytest

from project_service import config, metrics
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


def test_unbounded_lists_now_paginated(tmp_path):
    # M1: list_chats / list_chat_snapshots / list_folders / list_knowledge_files
    # / list_snapshots / list_temp_attachments / list_artifact_refs must accept
    # limit+offset and cap at MAX_PAGE_SIZE when unbounded (no LIMIT -1 DoS).
    store = ProjectStore(db_path=tmp_path / "pag.db")
    pid = store.create_project({"name": "P"})["id"]
    for i in range(3):
        store.create_chat({"project_id": pid, "title": f"c{i}"})
    chats_capped = store.list_chats(pid)
    assert len(chats_capped) == 3
    page = store.list_chats(pid, limit=2, offset=1)
    assert len(page) == 2
    fid = store.create_folder({"project_id": pid, "name": "f"})["id"]
    store.create_knowledge_file({
        "project_id": pid, "folder_id": fid, "name": "k0",
        "original_name": "k0.txt", "file_path": str(tmp_path / "k0.txt"),
    })
    store.create_knowledge_file({
        "project_id": pid, "folder_id": fid, "name": "k1",
        "original_name": "k1.txt", "file_path": str(tmp_path / "k1.txt"),
    })
    kfiles = store.list_knowledge_files(pid, limit=1)
    assert len(kfiles) == 1
    store.create_artifact_ref({
        "project_id": pid, "artifact_id": "a1",
        "artifact_name": "art", "artifact_type": "html",
    })
    refs = store.list_artifact_refs(pid)
    assert len(refs) == 1


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


def test_uds_and_gateway_metrics_record():
    # M10/M12/M14: UDS dispatch + upstream gateway + identity counters surface
    # in snapshot so /metrics can report the UDS surface and a degraded upstream.
    metrics.reset()
    metrics.record_uds_request("project.list", 200)
    metrics.record_uds_request("project.list", -32601)
    metrics.record_gateway("rag", ok=True, retries=1, latency=0.05)
    metrics.record_gateway("rag", ok=False, retries=2, latency=0.1)
    metrics.record_identity_verify(ok=True)
    metrics.record_identity_verify(ok=False, cached=True)
    snap = metrics.snapshot()
    assert snap["uds_requests_total"]["project.list"] == 2
    assert snap["uds_requests_by_status"]["200"] == 1
    assert snap["uds_requests_by_status"]["-32601"] == 1
    assert snap["gateway"]["rag"]["requests"] == 2
    assert snap["gateway"]["rag"]["errors"] == 1
    assert snap["gateway"]["rag"]["retries"] == 3
    assert snap["gateway"]["rag"]["avg_latency_ms"] > 0
    assert snap["identity_verify_ok"] == 1
    assert snap["identity_verify_cached"] == 1
    metrics.reset()


async def test_rag_coordinator_to_sources(tmp_path):
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
    sources = await rc._to_sources(items, pid)
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


def test_identity_verify_negative_cache(tmp_path, monkeypatch):
    # M14: a failed verify is cached for the TTL so repeat bad-token requests
    # don't each block on a sync HTTP roundtrip.
    import httpx
    from project_service.engine.gateway_client import GatewayClient, GatewayError
    metrics.reset()
    monkeypatch.setattr(config, "IDENTITY_SERVICE_TOKEN", "svc-tok")
    monkeypatch.setattr(config, "IDENTITY_VERIFY_CACHE_TTL", 5)
    gc = GatewayClient()
    call_count = {"n": 0}

    class _FakeResp:
        status_code = 401

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "401", request=httpx.Request("POST", "x"), response=httpx.Response(401),
            )

    class _FakeClient:
        def post(self, *a, **k):
            call_count["n"] += 1
            return _FakeResp()

        def close(self):
            pass

    gc._verify_client = _FakeClient()
    # first call hits the network + caches the denial
    with pytest.raises(GatewayError):
        gc.identity_verify_sync("bad-token")
    assert call_count["n"] == 1
    # second call must be served from the negative cache — no network hit
    with pytest.raises(GatewayError):
        gc.identity_verify_sync("bad-token")
    assert call_count["n"] == 1
    snap = metrics.snapshot()
    assert snap["identity_verify_fail"] == 1
    assert snap["identity_verify_cached"] == 1
    metrics.reset()
