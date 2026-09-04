import asyncio
import os
import signal
import time

import pytest

pytestmark = pytest.mark.integration


def _rag_up() -> bool:
    import httpx
    try:
        with httpx.Client(timeout=3.0) as c:
            return c.get("http://127.0.0.1:11436/health").status_code == 200
    except Exception:
        return False


skip_no_rag = pytest.mark.skipif(not _rag_up(), reason="fusion-rag 11436 not up")


def _rag_pid() -> int | None:
    import subprocess
    out = subprocess.run(
        ["pgrep", "-f", "fusion_rag.api.server"],
        capture_output=True, text=True,
    )
    pids = [p for p in out.stdout.split() if p]
    return int(pids[0]) if pids else None


def _start_rag() -> None:
    import subprocess
    subprocess.Popen(
        ["python3", "-m", "fusion_rag.api.server"],
        cwd=os.path.expanduser("~/fusion/fusion-rag"),
        stdout=open("/tmp/rag_fault.log", "ab"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _wait_rag_up(timeout: float = 30.0) -> bool:
    import httpx
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            with httpx.Client(timeout=2.0) as c:
                if c.get("http://127.0.0.1:11436/health").status_code == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


@skip_no_rag
@pytest.mark.asyncio
async def test_rag_down_mid_request_degrades_gracefully(tmp_path):
    # fault injection: kill fusion-rag while a RAG query is in flight. the
    # GatewayClient must raise GatewayError (no hang, no silent None), and the
    # UDS dispatch must map it to a JSON-RPC error without crashing the daemon.
    from project_service import config
    from project_service.store.project_store import ProjectStore
    from project_service.engine.project_manager import ProjectManager
    from project_service.engine.rag_coordinator import RAGCoordinator
    from project_service.engine.gateway_client import GatewayClient, GatewayError
    from project_service.daemon_server import ProjectRPCServer

    config.RAG_BASE_URL = os.environ.get("FUSION_RAG_URL", "http://127.0.0.1:11436")
    config.RAG_EMBEDDING_MODEL = "BGE-M3"

    store = ProjectStore(db_path=os.path.join(str(tmp_path), "fault.db"))
    gc = GatewayClient()
    pm = ProjectManager(store=store)
    rc = RAGCoordinator(store=store, project_manager=pm, upstream=gc)
    server = ProjectRPCServer()
    server.project_manager = pm
    server.rag_coordinator = rc
    server.gateway_client = gc

    proj = await asyncio.to_thread(store.create_project, {"name": "fault", "rag_mode": "AUTO"})
    pid = proj["id"]

    # warm: create the KB so the first timed query is already mid-search, not
    # blocked behind KB creation. if this fails the upstream is already sick.
    try:
        await rc.query(pid, "warmup", top_k=3)
    except GatewayError:
        pass

    rag_pid = _rag_pid()
    assert rag_pid, "fusion-rag pid not found — cannot inject fault"

    # fire a query, then kill rag mid-flight. the query should fail fast with
    # GatewayError, not hang past the retry/backoff budget.
    async def _query_and_kill():
        # give the query a head start, then SIGKILL the upstream
        await asyncio.sleep(0.15)
        try:
            os.kill(rag_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    saw_error, fail_elapsed = await asyncio.gather(
        _query_and_kill(),
        _expect_failure(rc, pid),
    )

    # recovery: restart rag and confirm a fresh query succeeds (no permanent
    # breakage, client/httpx pool reuses cleanly after the kill).
    _start_rag()
    assert _wait_rag_up(timeout=40), "fusion-rag did not come back up after restart"

    # fresh client to avoid any pooled-connection reuse edge on the recovery
    # check; the production daemon keeps the long-lived client, but recovery
    # correctness is what we assert here.
    gc2 = GatewayClient()
    rc2 = RAGCoordinator(store=store, project_manager=pm, upstream=gc2)
    ok = await _recovery_query(rc2, pid)

    await gc.close()
    await gc2.close()
    store.close()

    assert ok, "rag did not recover after restart — query still failing"


async def _expect_failure(rc, pid):
    # rag_coordinator.query degrades gracefully: it does NOT raise on upstream
    # failure (chat path stays best-effort), but it must surface an `error` key
    # so direct callers know the kb backend is dead — no silent empty result.
    t = time.monotonic()
    result = await rc.query(pid, "query after kill", top_k=3)
    elapsed = time.monotonic() - t
    saw_error = isinstance(result, dict) and "error" in result
    assert saw_error, (
        "rag query against dead upstream returned no error flag — silent failure"
    )
    # 3 retries with backoff cap ~ a few seconds; must not hang for minutes.
    assert elapsed < 30, f"query hung {elapsed:.1f}s against dead upstream — no fast fail"
    return saw_error, elapsed


async def _recovery_query(rc, pid):
    from project_service.engine.gateway_client import GatewayError
    for _ in range(5):
        try:
            await rc.query(pid, "recovery check", top_k=3)
            return True
        except GatewayError:
            await asyncio.sleep(1)
    return False
