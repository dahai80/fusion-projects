import httpx
import pytest

from project_service import config
from project_service.api.rest_server import create_app
from project_service.engine.knowledge_manager import KnowledgeError, _validate_source
from project_service.store.project_store import ProjectStore


@pytest.mark.asyncio
async def test_rest_auth_disabled_when_no_key(monkeypatch):
    monkeypatch.setattr(config, "REST_ALLOW_NO_AUTH", True)
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/projects")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_rest_startup_refuses_non_loopback_no_auth(monkeypatch):
    import pytest
    monkeypatch.setattr(config, "REST_API_KEY", "")
    monkeypatch.setattr(config, "REST_ALLOW_NO_AUTH", False)
    monkeypatch.setattr(config, "REST_HOST", "0.0.0.0")
    import project_service.api.rest_server as rs
    with pytest.raises(RuntimeError):
        app = create_app()
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_auth_off_warns_critical_when_unacknowledged(monkeypatch, caplog):
    monkeypatch.setattr(config, "REST_API_KEY", "")
    monkeypatch.setattr(config, "REST_ALLOW_NO_AUTH", False)
    from project_service.api import security
    security.AuthMiddleware._warned_no_auth = False
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    with caplog.at_level("CRITICAL"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/api/v1/projects")
    assert any("UNAUTHENTICATED" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_auth_off_warns_once_when_acknowledged(monkeypatch, caplog):
    monkeypatch.setattr(config, "REST_API_KEY", "")
    monkeypatch.setattr(config, "REST_ALLOW_NO_AUTH", True)
    from project_service.api import security
    security.AuthMiddleware._warned_no_auth = False
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    with caplog.at_level("WARNING"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/api/v1/projects")
            await client.get("/api/v1/projects")
    warnings = [rec for rec in caplog.records if "acknowledged" in rec.message]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_rest_auth_rejects_missing_token(monkeypatch):
    monkeypatch.setattr(config, "REST_API_KEY", "secret123")
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/projects")
        assert r.status_code == 401
        r = await client.get("/api/v1/projects", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 403
        r = await client.get("/api/v1/projects", headers={"Authorization": "Bearer secret123"})
        assert r.status_code == 200
        r = await client.get("/health")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_rest_auth_accepts_x_api_key(monkeypatch):
    monkeypatch.setattr(config, "REST_API_KEY", "secret123")
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/projects", headers={"x-api-key": "secret123"})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_rest_body_size_rejects_oversize(monkeypatch):
    monkeypatch.setattr(config, "REST_MAX_BODY_BYTES", 16)
    monkeypatch.setattr(config, "REST_API_KEY", "")
    monkeypatch.setattr(config, "REST_ALLOW_NO_AUTH", True)
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        big = {"name": "x" * 100}
        r = await client.post("/api/v1/projects", json=big)
        assert r.status_code == 413


@pytest.mark.asyncio
async def test_rest_rate_limit(monkeypatch):
    monkeypatch.setattr(config, "REST_RATE_LIMIT", 3)
    monkeypatch.setattr(config, "REST_RATE_WINDOW", 60.0)
    monkeypatch.setattr(config, "REST_API_KEY", "")
    monkeypatch.setattr(config, "REST_ALLOW_NO_AUTH", True)
    from project_service.api import security
    security.reset_rate_limiter()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        codes = []
        for _ in range(5):
            r = await client.get("/api/v1/projects")
            codes.append(r.status_code)
        assert 429 in codes
    security.reset_rate_limiter()


def test_validate_source_rejects_sensitive_dir():
    with pytest.raises(KnowledgeError):
        _validate_source("/etc/hosts")


def test_validate_source_rejects_sensitive_name(tmp_path):
    secret = tmp_path / "secret.key"
    secret.write_text("k")
    with pytest.raises(KnowledgeError):
        _validate_source(str(secret))


def test_validate_source_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "KNOWLEDGE_MAX_FILE_BYTES", 8)
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 100)
    with pytest.raises(KnowledgeError):
        _validate_source(str(big))


def test_validate_source_accepts_normal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "KNOWLEDGE_MAX_FILE_BYTES", 1024)
    f = tmp_path / "ok.txt"
    f.write_text("hello")
    src = _validate_source(str(f))
    assert src.exists()


def test_socket_mode_default_secure():
    assert config.SOCKET_MODE == 0o600


def test_sqlite_wal_mode(tmp_path):
    s = ProjectStore(db_path=tmp_path / "t.db")
    with s._cursor() as cur:
        mode = cur.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        bt = cur.execute("PRAGMA busy_timeout").fetchone()[0]
        assert bt == 5000
    s.close()
