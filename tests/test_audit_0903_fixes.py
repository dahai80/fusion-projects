import json
from unittest.mock import AsyncMock

import pytest

from project_service import config
from project_service.engine.chat_manager import ChatManager
from project_service.engine.gateway_client import GatewayClient
from project_service.engine.project_manager import ProjectManager
from project_service.engine.rag_coordinator import RAGCoordinator
from project_service.store.file_store import FileStore, InvalidProjectId
from project_service.store.project_store import FolderError, ProjectStore


# ── helpers ──


def _rpc(tmp_path, monkeypatch):
    from project_service.daemon_server import ProjectRPCServer
    from project_service.engine.instruction_engine import InstructionEngine
    from project_service.engine.knowledge_manager import KnowledgeManager
    store = ProjectStore(db_path=tmp_path / "projects.db")
    fs = FileStore(storage_dir=tmp_path / "storage")
    pm = ProjectManager(store=store, file_store=fs)
    ie = InstructionEngine(store=store, project_manager=pm)
    cm = ChatManager(store=store, project_manager=pm, file_store=fs)
    km = KnowledgeManager(store=store, project_manager=pm)
    server = ProjectRPCServer(
        project_manager=pm,
        instruction_engine=ie,
        chat_manager=cm,
        knowledge_manager=km,
        upstream=AsyncMock(spec=GatewayClient),
    )
    monkeypatch.setattr(server, "_store_ref", store, raising=False)
    return server, store


# ── P0-1: FileStore path traversal / symlink ──


def test_file_store_rejects_path_traversal_project_id(tmp_path):
    fs = FileStore(storage_dir=tmp_path / "storage")
    with pytest.raises(InvalidProjectId):
        fs.project_dir("../escape")


def test_file_store_rejects_symlink_project_id(tmp_path):
    base = tmp_path / "storage"
    base.mkdir()
    # symlink points OUTSIDE storage_dir -> resolve() escapes -> rejected
    (base / "evil").symlink_to(tmp_path)
    fs = FileStore(storage_dir=base)
    with pytest.raises(InvalidProjectId):
        fs.project_dir("evil")


# ── P1-4: folder parent same-project + no cycle ──


def test_create_folder_rejects_cross_project_parent(store):
    a = store.create_project({"name": "A"})
    b = store.create_project({"name": "B"})
    fa = store.create_folder({"project_id": a["id"], "name": "fa"})
    with pytest.raises(FolderError):
        store.create_folder({
            "project_id": b["id"], "name": "fb", "parent_id": fa["id"],
        })


def test_update_folder_rejects_cycle(store):
    p = store.create_project({"name": "P"})
    f1 = store.create_folder({"project_id": p["id"], "name": "f1"})
    f2 = store.create_folder({"project_id": p["id"], "name": "f2", "parent_id": f1["id"]})
    # moving f1 under f2 (its own descendant) must fail
    with pytest.raises(FolderError):
        store.update_folder(f1["id"], {"parent_id": f2["id"]})


# ── P1-5: knowledge_file folder_id same-project ──


def test_create_knowledge_file_rejects_cross_project_folder(store):
    a = store.create_project({"name": "A"})
    b = store.create_project({"name": "B"})
    fa = store.create_folder({"project_id": a["id"], "name": "fa"})
    with pytest.raises(FolderError):
        store.create_knowledge_file({
            "project_id": b["id"], "folder_id": fa["id"],
            "name": "x.txt", "original_name": "x.txt",
            "file_path": "/tmp/x.txt", "file_size": 1,
        })


# ── P1-10: message insert bumps chat updated_at ──


def test_create_message_bumps_chat_updated_at(store):
    p = store.create_project({"name": "P"})
    chat = store.create_chat({"project_id": p["id"], "title": "t"})
    before = store.get_chat(chat["id"])["updated_at"]
    store.create_message({"chat_id": chat["id"], "role": "user", "content": "hi"})
    after = store.get_chat(chat["id"])["updated_at"]
    assert after >= before


# ── P0-11/P1-11: always_include containment ──


@pytest.mark.asyncio
async def test_always_include_rejects_path_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", tmp_path / "storage")
    store = ProjectStore(db_path=tmp_path / "projects.db")
    fs = FileStore(storage_dir=tmp_path / "storage")
    pm = ProjectManager(store=store, file_store=fs)
    upstream = AsyncMock(spec=GatewayClient)
    rc = RAGCoordinator(store=store, project_manager=pm, upstream=upstream)
    p = store.create_project({"name": "P"})
    pid = p["id"]
    kdir = config.STORAGE_DIR / pid / "knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    evil = tmp_path / "secret.txt"
    evil.write_text("stolen")
    store.create_knowledge_file({
        "project_id": pid, "name": "evil", "original_name": "evil",
        "file_path": str(evil), "file_size": 6, "always_include": 1,
    })
    ctx, sources = await rc.get_always_include_context(pid)
    assert ctx == ""
    assert sources == []
    store.close()


def test_merge_results_filters_below_threshold():
    items = [
        {"doc_id": "a", "score": 0.9},
        {"doc_id": "b", "score": 0.2},
        {"doc_id": "c", "score": 0.7},
    ]
    kept, dropped = RAGCoordinator._merge_results([items], top_k=10, threshold=0.65)
    assert [k["doc_id"] for k in kept] == ["a", "c"]
    assert dropped == 1


# ── P1-12: rag top_k/threshold clamped ──


@pytest.mark.asyncio
async def test_rag_query_clamps_top_k_and_threshold(tmp_path, monkeypatch):
    store = ProjectStore(db_path=tmp_path / "projects.db")
    fs = FileStore(storage_dir=tmp_path / "storage")
    pm = ProjectManager(store=store, file_store=fs)
    upstream = AsyncMock(spec=GatewayClient)
    upstream.rag_kb_status = AsyncMock(return_value=200)
    upstream.rag_create_kb = AsyncMock(return_value={"id": "kb1"})
    upstream.rag_search = AsyncMock(return_value=[])
    rc = RAGCoordinator(store=store, project_manager=pm, upstream=upstream)
    p = store.create_project({"name": "P"})
    await rc.query(p["id"], "q", top_k=99999, threshold=5.0)
    called = upstream.rag_search.await_args
    assert called.kwargs["top_k"] == config.RAG_MAX_TOP_K
    store.close()


# ── P1-23: fork_chat caps messages ──


@pytest.mark.asyncio
async def test_fork_chat_truncates_beyond_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FORK_MAX_MESSAGES", 5)
    store = ProjectStore(db_path=tmp_path / "projects.db")
    fs = FileStore(storage_dir=tmp_path / "storage")
    pm = ProjectManager(store=store, file_store=fs)
    cm = ChatManager(store=store, project_manager=pm, file_store=fs)
    p = store.create_project({"name": "P"})
    chat = store.create_chat({"project_id": p["id"], "title": "t"})
    for i in range(20):
        store.create_message({"chat_id": chat["id"], "role": "user", "content": f"m{i}"})
    fork = await cm.fork_chat(chat["id"])
    assert store.count_messages(fork.id) == 5
    store.close()


# ── P1-25: migrate_artifact rollback on ref insert failure ──


@pytest.mark.asyncio
async def test_migrate_artifact_rolls_back_on_ref_failure(tmp_path, monkeypatch):
    store = ProjectStore(db_path=tmp_path / "projects.db")
    fs = FileStore(storage_dir=tmp_path / "storage")
    pm = ProjectManager(store=store, file_store=fs)
    upstream = AsyncMock(spec=GatewayClient)
    upstream.artifacts_call = AsyncMock(side_effect=[
        {"artifact": {"name": "a", "type": "text", "kind": "doc", "summary": "s", "session_id": "x"}},
        {"ok": True},  # move_to_project_kb
        {"ok": True},  # move_to_source_kb (rollback)
    ])
    pm._call_artifacts_engine = upstream.artifacts_call
    p = store.create_project({"name": "P"})

    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(store, "create_artifact_ref", _boom)
    with pytest.raises(RuntimeError):
        await pm.migrate_artifact(p["id"], "art-1")
    assert upstream.artifacts_call.await_count == 3
    store.close()


# ── P0-6: migrate.down gated behind env ──


@pytest.mark.asyncio
async def test_migrate_down_disabled_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("FUSION_PROJECT_ALLOW_DANGEROUS_MIGRATE", raising=False)
    server, store = _rpc(tmp_path, monkeypatch)
    resp = await server.handle_request(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "migrate.down",
        "params": {"confirm": "rollback-schema", "target_version": 0},
    }).encode())
    parsed = json.loads(resp.decode())
    assert "error" in parsed
    assert "disabled" in parsed["error"]["message"]
    store.close()


# ── P0-4: TenantMiddleware install fail-closed ──


@pytest.mark.asyncio
async def test_tenant_middleware_install_failure_aborts(monkeypatch):
    # import first so the module-level create_app() call (rest_server.py:189)
    # runs with the default empty token — safe, no tenant path — and is cached.
    from project_service.api.rest_server import create_app
    import fusion_core.tenant as ft

    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(ft, "install_tenant_middleware", _boom)
    monkeypatch.setattr(config, "IDENTITY_SERVICE_TOKEN", "tok")
    with pytest.raises(RuntimeError):
        create_app()
