import logging

import httpx
import pytest

from project_service import config
from project_service.store.project_store import ProjectStore

logger = logging.getLogger(__name__)


def _mw_classes(app):
    return [m.cls for m in app.user_middleware]

logger = logging.getLogger(__name__)


@pytest.fixture
def store(tmp_path) -> ProjectStore:
    return ProjectStore(db_path=tmp_path / "t.db")


def _set_tenant(tenant_id: str):
    from fusion_core.tenant import TenantContext, set_context
    return set_context(TenantContext(tenant_id=tenant_id))


def test_create_stamps_tenant_from_context(store: ProjectStore):
    token = _set_tenant("tenantA")
    try:
        p = store.create_project({"name": "A-proj"})
        assert p["tenant_id"] == "tenantA"
    finally:
        from fusion_core.tenant import reset
        reset(token)


def test_no_context_disables_enforcement(store: ProjectStore):
    p = store.create_project({"name": "bare"})
    assert p["tenant_id"] == ""
    assert store.get_project(p["id"]) is not None
    rows = store.list_projects()
    assert any(r["id"] == p["id"] for r in rows)


def test_list_filters_by_tenant(store: ProjectStore):
    tok_a = _set_tenant("tenantA")
    pa = store.create_project({"name": "A1"})
    from fusion_core.tenant import reset
    reset(tok_a)
    tok_b = _set_tenant("tenantB")
    pb = store.create_project({"name": "B1"})
    try:
        rows = store.list_projects()
        ids = {r["id"] for r in rows}
        assert pb["id"] in ids
        assert pa["id"] not in ids
    finally:
        reset(tok_b)


def test_cross_tenant_get_denied(store: ProjectStore):
    tok_a = _set_tenant("tenantA")
    pa = store.create_project({"name": "A-secret"})
    from fusion_core.tenant import reset
    reset(tok_a)
    tok_b = _set_tenant("tenantB")
    try:
        assert store.get_project(pa["id"]) is None
    finally:
        reset(tok_b)


def test_cross_tenant_update_denied(store: ProjectStore):
    tok_a = _set_tenant("tenantA")
    pa = store.create_project({"name": "A"})
    from fusion_core.tenant import reset
    reset(tok_a)
    tok_b = _set_tenant("tenantB")
    try:
        assert store.update_project(pa["id"], {"name": "hijack"}) is None
        from fusion_core.tenant import reset as _r
        _r(tok_b)
        tok_a2 = _set_tenant("tenantA")
        assert store.get_project(pa["id"])["name"] == "A"
        reset(tok_a2)
    finally:
        pass


def test_cross_tenant_delete_denied(store: ProjectStore):
    tok_a = _set_tenant("tenantA")
    pa = store.create_project({"name": "A"})
    from fusion_core.tenant import reset
    reset(tok_a)
    tok_b = _set_tenant("tenantB")
    try:
        assert store.delete_project(pa["id"]) is False
    finally:
        reset(tok_b)
    tok_a2 = _set_tenant("tenantA")
    try:
        assert store.get_project(pa["id"]) is not None
    finally:
        reset(tok_a2)


# ── REST middleware fail-closed ──


@pytest.fixture
def rest_app(tmp_path, monkeypatch):
    monkeypatch.setenv("FUSION_IDENTITY_SERVICE_TOKEN", "svc-secret")
    monkeypatch.setenv("FUSION_PROJECT_HOME", str(tmp_path / "fp"))
    import importlib
    import project_service.config as cfg_mod
    importlib.reload(cfg_mod)
    import project_service.engine.gateway_client as gw_mod
    importlib.reload(gw_mod)
    import project_service.api.rest_server as rs_mod
    importlib.reload(rs_mod)
    app = rs_mod.create_app()
    return app, rs_mod


async def test_rest_missing_tenant_header_401(rest_app):
    app, _ = rest_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/projects")
        assert r.status_code == 401


async def test_rest_exempt_paths_pass(rest_app):
    app, _ = rest_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
        assert r.status_code == 200


def test_rest_tenant_middleware_installed(rest_app):
    app, _ = rest_app
    from fusion_core.tenant import TenantMiddleware
    found = TenantMiddleware in _mw_classes(app)
    assert found, "TenantMiddleware should be installed when IDENTITY_SERVICE_TOKEN set"


def test_rest_no_identity_token_keeps_auth_middleware(tmp_path, monkeypatch):
    monkeypatch.delenv("FUSION_IDENTITY_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("FUSION_PROJECT_HOME", str(tmp_path / "fp2"))
    import importlib
    import project_service.config as cfg_mod
    importlib.reload(cfg_mod)
    import project_service.engine.gateway_client as gw_mod
    importlib.reload(gw_mod)
    import project_service.api.rest_server as rs_mod
    importlib.reload(rs_mod)
    app = rs_mod.create_app()
    from project_service.api.security import AuthMiddleware
    found = AuthMiddleware in _mw_classes(app)
    assert found, "AuthMiddleware should remain when no identity token"


@pytest.fixture(autouse=True)
def _restore_modules_after_tenant_test():
    # rest_app / test_rest_* reload config/gateway_client/rest_server with a
    # tenant env; monkeypatch restores the OS env but the reloaded module
    # objects keep captured values (e.g. IDENTITY_SERVICE_TOKEN="svc-secret").
    # without this teardown, later modules importing create_app see the
    # tenant-gated config and get 401. reload the three modules back to a
    # clean no-tenant state after every test in this file.
    yield
    import importlib
    import os
    os.environ.pop("FUSION_IDENTITY_SERVICE_TOKEN", None)
    import project_service.config as cfg_mod
    importlib.reload(cfg_mod)
    import project_service.engine.gateway_client as gw_mod
    importlib.reload(gw_mod)
    import project_service.api.rest_server as rs_mod
    importlib.reload(rs_mod)
