import importlib
import os
from unittest import mock

import pytest

import project_service.config as config


def _reload_config(monkeypatch):
    return importlib.reload(config)


class TestIssue33ProcessTitle:
    def test_set_process_title_actual(self):
        setproctitle = pytest.importorskip("setproctitle")
        from project_service.proc_title import set_process_title

        set_process_title("fusion-projects test-daemon")
        assert "fusion-projects" in setproctitle.getproctitle()

    def test_set_process_title_fail_soft(self, monkeypatch):
        import sys

        import project_service.proc_title as pt

        # sys.modules None entries make "import fusion_core" / "import
        # setproctitle" raise ImportError without touching builtins.__import__
        # (which pytest internals also rely on).
        monkeypatch.setitem(sys.modules, "fusion_core", None)
        monkeypatch.setitem(sys.modules, "setproctitle", None)
        pt.set_process_title("should-not-raise")

    def test_daemon_main_sets_title(self, monkeypatch):
        called = {}
        monkeypatch.setattr("project_service.proc_title.set_process_title", lambda t: called.setdefault("t", t))

        import asyncio

        def fake_run(_coro=None):
            called["ran"] = True

        monkeypatch.setattr(asyncio, "run", fake_run)
        monkeypatch.setattr("project_service.logging_config.setup_logging", lambda p: None)
        from project_service import daemon_server

        daemon_server.main()
        assert called["t"] == "fusion-projects daemon"
        assert called["ran"]


class TestIssue34ConfigMigration:
    def test_gateway_api_key_per_service_env_wins(self, monkeypatch):
        monkeypatch.setenv("FUSION_GATEWAY_API_KEY", "per-service-key")
        monkeypatch.setenv("FUSION_CLIENT_KEY", "client-key")
        cfg = _reload_config(monkeypatch)
        try:
            assert cfg.GATEWAY_API_KEY == "per-service-key"
        finally:
            importlib.reload(config)

    def test_gateway_api_key_client_key_chain(self, monkeypatch):
        for var in ("FUSION_GATEWAY_API_KEY", "FUSION_MLX_API_KEY", "MLX_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("FUSION_CLIENT_KEY", "client-key")
        cfg = _reload_config(monkeypatch)
        try:
            assert cfg.GATEWAY_API_KEY == "client-key"
        finally:
            importlib.reload(config)

    def test_gateway_api_key_dev_empty_no_raise(self, monkeypatch):
        for var in ("FUSION_GATEWAY_API_KEY", "FUSION_CLIENT_KEY", "FUSION_MLX_API_KEY", "MLX_API_KEY", "FUSION_PROFILE"):
            monkeypatch.delenv(var, raising=False)
        cfg = _reload_config(monkeypatch)
        try:
            assert cfg.GATEWAY_API_KEY == ""
        finally:
            importlib.reload(config)

    def test_gateway_api_key_prod_missing_raises(self, monkeypatch):
        for var in ("FUSION_GATEWAY_API_KEY", "FUSION_CLIENT_KEY", "FUSION_MLX_API_KEY", "MLX_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("FUSION_PROFILE", "prod")
        try:
            _reload_config(monkeypatch)
            raise AssertionError("expected RuntimeError in prod without key")
        except RuntimeError as exc:
            assert "FUSION_CLIENT_KEY" in str(exc)
        # restore before the final reload: monkeypatch undo happens at teardown,
        # which is after this line — the reload would re-raise in prod mode.
        monkeypatch.delenv("FUSION_PROFILE", raising=False)
        importlib.reload(config)

    def test_rest_port_service_port_resolution(self, monkeypatch):
        monkeypatch.setenv("FUSION_PROJECT_PORT", "21555")
        cfg = _reload_config(monkeypatch)
        try:
            assert cfg.REST_PORT == 21555
        finally:
            importlib.reload(config)

    def test_rest_port_default(self, monkeypatch):
        monkeypatch.delenv("FUSION_PROJECT_PORT", raising=False)
        cfg = _reload_config(monkeypatch)
        try:
            assert cfg.REST_PORT == 11440
        finally:
            importlib.reload(config)

    def test_base_dir_legacy_home_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUSION_PROJECT_HOME", str(tmp_path))
        cfg = _reload_config(monkeypatch)
        try:
            assert cfg.BASE_DIR == tmp_path
        finally:
            importlib.reload(config)

    def test_base_dir_canonical_data_dir_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FUSION_PROJECT_HOME", raising=False)
        monkeypatch.setenv("FUSION_PROJECT_DATA_DIR", str(tmp_path))
        cfg = _reload_config(monkeypatch)
        try:
            assert cfg.BASE_DIR == tmp_path
        finally:
            monkeypatch.delenv("FUSION_PROJECT_DATA_DIR", raising=False)
            importlib.reload(config)

    def test_gateway_url_default(self, monkeypatch):
        for var in ("FUSION_GATEWAY_URL", "FUSION_GATEWAY_BASE_URL"):
            monkeypatch.delenv(var, raising=False)
        cfg = _reload_config(monkeypatch)
        try:
            assert cfg.GATEWAY_URL == "http://127.0.0.1:11432"
        finally:
            importlib.reload(config)

    def test_no_local_legacy_key_names_in_source(self):
        src = open("project_service/config.py", encoding="utf-8").read()
        assert "FUSION_MLX_API_KEY" not in src
        assert "MLX_API_KEY" not in src
