import json
from unittest.mock import AsyncMock

import httpx
import pytest

from project_service import config
from project_service.api.rest_server import create_app
from project_service.daemon_server import ProjectRPCServer
from project_service.engine.chat_manager import ChatManager
from project_service.engine.gateway_client import GatewayClient
from project_service.engine.instruction_engine import InstructionEngine
from project_service.engine.knowledge_manager import KnowledgeManager, KnowledgeQuotaExceeded
from project_service.engine.project_manager import ProjectManager
from project_service.store.file_store import FileStore
from project_service.store.project_store import ProjectStore


# ── E5: rate limiter X-Forwarded-For + IP cap eviction ──


@pytest.mark.asyncio
async def test_rate_limit_honors_xff(monkeypatch):
    monkeypatch.setattr(config, "REST_RATE_LIMIT", 2)
    monkeypatch.setattr(config, "REST_RATE_WINDOW", 60.0)
    monkeypatch.setattr(config, "REST_API_KEY", "")
    from project_service.api import security
    security.reset_rate_limiter()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # client A: 2 requests (at limit) via X-Forwarded-For 203.0.113.1
        for _ in range(2):
            r = await client.get("/api/v1/projects", headers={"X-Forwarded-For": "203.0.113.1"})
            assert r.status_code != 429
        # client A 3rd request: now over limit
        r = await client.get("/api/v1/projects", headers={"X-Forwarded-For": "203.0.113.1"})
        assert r.status_code == 429
        # client B (different XFF) not affected by client A's bucket
        r = await client.get("/api/v1/projects", headers={"X-Forwarded-For": "198.51.100.7"})
        assert r.status_code != 429
    security.reset_rate_limiter()


@pytest.mark.asyncio
async def test_rate_limiter_evicts_at_ip_cap(monkeypatch):
    from project_service.api import security
    monkeypatch.setattr(config, "RATE_MAX_IPS", 3)
    monkeypatch.setattr(config, "REST_RATE_LIMIT", 100)
    monkeypatch.setattr(config, "REST_RATE_WINDOW", 60.0)
    security.reset_rate_limiter()
    limiter = security._get_rate_limiter()
    for i in range(10):
        limiter.check(f"10.0.0.{i}")
    assert len(limiter._hits) <= config.RATE_MAX_IPS
    security.reset_rate_limiter()


# ── P3: tenant-level rate limiting ──


def test_rate_key_uses_tenant_when_context_present(monkeypatch):
    from project_service.api import security
    from fusion_core.tenant.context import TenantContext, set_context, reset as reset_ctx

    ctx = TenantContext(tenant_id="tenant-42", user_id="u1")
    token = set_context(ctx)
    try:
        key = security.RateLimitMiddleware._rate_key(
            type("R", (), {"headers": {}, "client": type("C", (), {"host": "9.9.9.9"})()})()
        )
        assert key == "tenant:tenant-42"
    finally:
        reset_ctx(token)


def test_rate_key_falls_back_to_ip_without_tenant():
    from project_service.api import security

    req = type("R", (), {"headers": {}, "client": type("C", (), {"host": "9.9.9.9"})()})()
    assert security.RateLimitMiddleware._rate_key(req) == "9.9.9.9"


def test_rate_key_honors_xff_without_tenant():
    from project_service.api import security

    req = type(
        "R",
        (),
        {"headers": {"x-forwarded-for": "203.0.113.9, 10.0.0.1"}, "client": None},
    )()
    assert security.RateLimitMiddleware._rate_key(req) == "203.0.113.9"


# ── P2: structured JSON logging ──


def test_json_formatter_emits_tenant(monkeypatch, tmp_path):
    import io
    import logging

    from project_service.logging_config import _JsonFormatter
    from fusion_core.tenant.context import TenantContext, set_context, reset as reset_ctx

    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(_JsonFormatter())
    lg = logging.getLogger("test_json_fmt")
    lg.handlers = [h]
    lg.setLevel(logging.INFO)
    lg.propagate = False
    ctx = TenantContext(tenant_id="tenant-7", user_id="u7")
    token = set_context(ctx)
    try:
        lg.info("hello %s", "world")
    finally:
        reset_ctx(token)
    import json as _json

    rec = _json.loads(buf.getvalue())
    assert rec["msg"] == "hello world"
    assert rec["tenant_id"] == "tenant-7"
    assert rec["user_id"] == "u7"
    assert rec["level"] == "INFO"


def test_json_formatter_no_tenant():
    import io
    import json as _json
    import logging

    from project_service.logging_config import _JsonFormatter

    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(_JsonFormatter())
    lg = logging.getLogger("test_json_fmt2")
    lg.handlers = [h]
    lg.setLevel(logging.INFO)
    lg.propagate = False
    lg.info("plain message")
    rec = _json.loads(buf.getvalue())
    assert rec["msg"] == "plain message"
    assert "tenant_id" not in rec


# ── B9/E1: disk quota rejects upload ──


def test_file_store_quota_rejects_over_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "KNOWLEDGE_PROJECT_QUOTA_BYTES", 50)
    fs = FileStore(storage_dir=tmp_path / "storage")
    fs.init_project("p1")
    kdir = fs.project_dir("p1") / "knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    (kdir / "existing.txt").write_bytes(b"x" * 40)
    from project_service.store.file_store import QuotaExceeded
    with pytest.raises(QuotaExceeded):
        fs.check_quota("p1", 20)


def test_file_store_quota_allows_under_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "KNOWLEDGE_PROJECT_QUOTA_BYTES", 200)
    fs = FileStore(storage_dir=tmp_path / "storage")
    fs.init_project("p1")
    kdir = fs.project_dir("p1") / "knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    (kdir / "existing.txt").write_bytes(b"x" * 40)
    fs.check_quota("p1", 50)


# ── B5: streaming export over UDS ──


@pytest.fixture
def rpc(tmp_path):
    store = ProjectStore(db_path=tmp_path / "projects.db")
    fs = FileStore(storage_dir=tmp_path / "storage")
    pm = ProjectManager(store=store, file_store=fs)
    ie = InstructionEngine(store=store, project_manager=pm)
    cm = ChatManager(store=store, project_manager=pm, file_store=fs)
    km = KnowledgeManager(store=store, project_manager=pm)
    fake_upstream = AsyncMock(spec=GatewayClient)
    server = ProjectRPCServer(
        project_manager=pm,
        instruction_engine=ie,
        chat_manager=cm,
        knowledge_manager=km,
        upstream=fake_upstream,
    )
    yield server
    store.close()


def _req(method, params=None):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()


def _parse(resp):
    return json.loads(resp.decode())


# ── B5: streaming export over UDS ──


@pytest.mark.asyncio
async def test_export_stream_chunks(rpc, monkeypatch):
    monkeypatch.setattr(config, "EXPORT_CHUNK_BYTES", 16)
    proj = await rpc.dispatch("project.create", {"name": "stream-proj"})
    pid = proj["id"]
    collected = bytearray()
    chunks_seen = 0
    done_seen = False
    export_id = None

    async def emit(delta):
        nonlocal chunks_seen, done_seen, export_id
        if delta.get("done"):
            done_seen = True
            export_id = delta.get("export_id")
            return
        collected.extend(__import__("base64").b64decode(delta["chunk"]))
        chunks_seen += 1

    result = await rpc.dispatch(
        "project.export.stream",
        {"project_id": pid, "stream": True},
        emit=emit,
    )
    assert result["streamed"] is True
    assert result["total_size"] == len(collected)
    assert done_seen is True
    assert export_id is not None
    assert chunks_seen >= 1
    # reassemble is a valid zip
    import zipfile, io
    with zipfile.ZipFile(io.BytesIO(bytes(collected))) as zf:
        assert "project.json" in zf.namelist()


@pytest.mark.asyncio
async def test_export_inline_rejects_oversize(rpc, monkeypatch):
    monkeypatch.setattr(config, "EXPORT_INLINE_MAX_BYTES", 1)
    proj = await rpc.dispatch("project.create", {"name": "big-proj"})
    pid = proj["id"]
    resp = await rpc.handle_request(
        _req("project.export", {"project_id": pid})
    )
    parsed = _parse(resp)
    assert "error" in parsed
    assert parsed["error"]["code"] == -32603


@pytest.mark.asyncio
async def test_export_stream_registered_in_rpc_list(rpc):
    methods = await rpc.dispatch("rpc.list", {})
    assert "project.export.stream" in methods["methods"]


# ── E6: PID identity check (start.sh logic) ──


def test_pid_identity_check_logic():
    # the start.sh _pid_is_daemon matches cmdline containing the entry module.
    # here we assert the matching pattern the script uses is present.
    import re
    script = open("start.sh").read()
    assert "project_service.daemon_server" in script
    # portable atomic lock: mkdir-based (no flock dependency, works on macOS + Linux).
    assert 'mkdir "$PID_FILE.lock"' in script
    assert re.search(r"_pid_is_daemon", script)


# ── H3: per-upstream isolated AsyncClient pools ──


def test_gateway_client_per_upstream_clients():
    gc = GatewayClient()
    assert gc._http_gateway is not gc._http_rag
    assert gc._http_gateway is not gc._http_agent
    assert gc._http_gateway is not gc._http_artifacts
    assert gc._http_rag is not gc._http_agent
    assert gc._http_rag is not gc._http_artifacts
    assert gc._http_agent is not gc._http_artifacts
    assert gc._client_for(gc._gateway_url) is gc._http_gateway
    assert gc._client_for(gc._rag_url) is gc._http_rag
    assert gc._client_for(gc._agent_url) is gc._http_agent
    assert gc._client_for(gc._artifacts_url) is gc._http_artifacts
    # unknown host falls back to gateway pool (not crash)
    assert gc._client_for("http://127.0.0.1:9999") is gc._http_gateway
    import asyncio
    asyncio.run(gc.close())


# ── H3 fan-out: index_folder concurrent with semaphore ──


@pytest.mark.asyncio
async def test_index_folder_concurrent(tmp_path):
    from project_service.models.project import ProjectCreate
    from project_service.engine.rag_coordinator import RAGCoordinator
    store = ProjectStore(db_path=tmp_path / "projects.db")
    fs = FileStore(storage_dir=tmp_path / "storage")
    pm = ProjectManager(store=store, file_store=fs)
    upstream = AsyncMock(spec=GatewayClient)
    upstream.rag_kb_status = AsyncMock(return_value=200)
    upstream.rag_create_kb = AsyncMock(return_value={"id": "kb-1"})
    upstream.rag_upload_doc = AsyncMock(return_value={"doc_id": "doc-x"})
    rc = RAGCoordinator(store=store, project_manager=pm, upstream=upstream)

    proj = await pm.create(ProjectCreate(name="idx-proj"))
    pid = proj.id
    folder = store.create_folder({"project_id": pid, "name": "f1"})
    fid = folder["id"]
    kdir = fs.project_dir(pid) / "knowledge" / fid
    kdir.mkdir(parents=True, exist_ok=True)
    for i in range(6):
        p = kdir / f"f{i}.txt"
        p.write_text("hello")
        store.create_knowledge_file({
            "project_id": pid, "folder_id": fid,
            "name": f"f{i}.txt", "original_name": f"f{i}.txt",
            "file_path": str(p), "file_size": 5, "index_status": "PENDING",
        })

    results = await rc.index_folder(fid, project_id=pid)
    assert len(results) == 6
    assert upstream.rag_upload_doc.await_count == 6
    store.close()


@pytest.mark.asyncio
async def test_index_folder_kb_lock_no_duplicate_create(tmp_path):
    from project_service.models.project import ProjectCreate
    from project_service.engine.rag_coordinator import RAGCoordinator
    store = ProjectStore(db_path=tmp_path / "projects.db")
    fs = FileStore(storage_dir=tmp_path / "storage")
    pm = ProjectManager(store=store, file_store=fs)
    upstream = AsyncMock(spec=GatewayClient)
    upstream.rag_kb_status = AsyncMock(return_value=200)
    upstream.rag_create_kb = AsyncMock(return_value={"id": "kb-1"})
    upstream.rag_upload_doc = AsyncMock(return_value={"doc_id": "doc-x"})
    rc = RAGCoordinator(store=store, project_manager=pm, upstream=upstream)

    proj = await pm.create(ProjectCreate(name="lock-proj"))
    pid = proj.id
    folder = store.create_folder({"project_id": pid, "name": "f1"})
    fid = folder["id"]
    kdir = fs.project_dir(pid) / "knowledge" / fid
    kdir.mkdir(parents=True, exist_ok=True)
    for i in range(8):
        p = kdir / f"f{i}.txt"
        p.write_text("hello")
        store.create_knowledge_file({
            "project_id": pid, "folder_id": fid,
            "name": f"f{i}.txt", "original_name": f"f{i}.txt",
            "file_path": str(p), "file_size": 5, "index_status": "PENDING",
        })

    await rc.index_folder(fid, project_id=pid)
    # kb created exactly once despite 8 concurrent index_file calls
    assert upstream.rag_create_kb.await_count == 1
    store.close()


# ── H3 fan-out: upstream health checks run concurrently ──


@pytest.mark.asyncio
async def test_upstream_health_concurrent(rpc):
    rpc.gateway_client.gateway_is_healthy = AsyncMock(return_value=True)
    rpc.gateway_client.rag_is_healthy = AsyncMock(return_value=True)
    rpc.gateway_client.agent_studio_is_healthy = AsyncMock(return_value=False)
    result = await rpc.dispatch("project.upstream.health", {})
    assert result == {"gateway": True, "rag": True, "agent_studio": False}

