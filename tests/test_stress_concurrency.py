import asyncio
import os
import statistics
import tempfile
import time

import pytest

pytestmark = pytest.mark.integration


def _upstreams_up() -> bool:
    import httpx
    try:
        with httpx.Client(timeout=3.0) as c:
            for url, key in [
                ("http://127.0.0.1:11434/health", os.environ.get("FUSION_E2E_MLX_API_KEY", "fg-admin-key")),
            ]:
                headers = {"Authorization": f"Bearer {key}"} if key else {}
                if c.get(url, headers=headers).status_code != 200:
                    return False
        return True
    except Exception:
        return False


skip_no_upstream = pytest.mark.skipif(not _upstreams_up(), reason="mlx 11434 not up")


@skip_no_upstream
@pytest.mark.asyncio
async def test_concurrent_store_writes_do_not_block_event_loop(tmp_path):
    # B1 stress: fire N concurrent store-write RPCs (project.chat.message.add,
    # each hits sqlite via to_thread) WHILE a high-frequency event-loop tick
    # runs. if to_thread is correct, the tick keeps a steady cadence (loop not
    # blocked by sync sqlite). if any store call ran sync on the loop, the tick
    # would stall — gap >> median gap exposes it.
    from project_service import config
    from project_service.store.project_store import ProjectStore
    from project_service.store.file_store import FileStore
    from project_service.engine.project_manager import ProjectManager
    from project_service.engine.chat_manager import ChatManager
    from project_service.engine.gateway_client import GatewayClient
    from project_service.daemon_server import ProjectRPCServer

    store = ProjectStore(db_path=os.path.join(str(tmp_path), "stress.db"))
    fs = FileStore(storage_dir=os.path.join(str(tmp_path), "storage"))
    gc = GatewayClient()
    pm = ProjectManager(store=store, file_store=fs, upstream=gc)
    cm = ChatManager(store=store, project_manager=pm)
    server = ProjectRPCServer()
    server.project_manager = pm
    server.chat_manager = cm
    server.gateway_client = gc

    proj = await server.dispatch("project.create", {"name": "stress"})
    pid = proj["id"]
    chat = await server.dispatch("project.chat.create", {"project_id": pid, "title": "s"})
    cid = chat["id"]

    N = 80
    tick_gaps: list[float] = []

    async def _event_loop_tick():
        # record inter-tick gaps; a blocked loop shows up as one huge gap.
        last = time.monotonic()
        for _ in range(400):
            await asyncio.sleep(0.005)
            now = time.monotonic()
            tick_gaps.append(now - last)
            last = now

    async def _store_write(i: int):
        await server.dispatch("project.chat.message.add", {
            "chat_id": cid, "content": f"msg-{i}", "role": "user",
        })

    t0 = time.monotonic()
    await asyncio.gather(
        _event_loop_tick(),
        *[_store_write(i) for i in range(N)],
    )
    elapsed = time.monotonic() - t0

    # verify all writes landed BEFORE closing any resource. the store close
    # happens last so the verification read isn't racing teardown.
    msgs = await asyncio.to_thread(store.list_messages, cid, limit=500)
    await gc.close()
    store.close()
    assert len(msgs) >= N, f"only {len(msgs)}/{N} writes persisted"

    # event-loop responsiveness: the worst tick gap must not dwarf the median.
    # a sync sqlite call blocking the loop would produce a gap >> 50ms median.
    median_gap = statistics.median(tick_gaps)
    max_gap = max(tick_gaps)
    block_ratio = max_gap / median_gap if median_gap > 0 else 0
    print(f"\n[B1 stress] N={N} writes in {elapsed:.2f}s "
          f"median_tick={median_gap*1000:.1f}ms max_tick={max_gap*1000:.1f}ms "
          f"block_ratio={block_ratio:.1f}x")
    # allow some scheduler jitter, but a true block is 20x+ median.
    assert block_ratio < 20, (
        f"event loop blocked: max_tick={max_gap*1000:.1f}ms "
        f"vs median={median_gap*1000:.1f}ms (ratio {block_ratio:.1f}x) — "
        f"sync store call ran on the loop thread"
    )


@skip_no_upstream
@pytest.mark.asyncio
async def test_concurrent_rag_queries_across_projects(tmp_path):
    # M6 stress: concurrent RAG queries across M projects must not serialize
    # behind a single global kb lock. each project gets its own kb-create lock,
    # so distinct projects proceed in parallel. measure wall-clock vs sum-of-
    # per-call to detect serialization.
    from project_service import config
    from project_service.store.project_store import ProjectStore
    from project_service.engine.project_manager import ProjectManager
    from project_service.engine.rag_coordinator import RAGCoordinator
    from project_service.engine.gateway_client import GatewayClient

    config.RAG_BASE_URL = os.environ.get("FUSION_RAG_URL", "http://127.0.0.1:11436")
    config.RAG_EMBEDDING_MODEL = os.environ.get("FUSION_RAG_EMBEDDING_MODEL", "BGE-M3")

    store = ProjectStore(db_path=os.path.join(str(tmp_path), "ragstress.db"))
    gc = GatewayClient()
    pm = ProjectManager(store=store)
    rc = RAGCoordinator(store=store, project_manager=pm, upstream=gc)

    M = 6
    pids = []
    for i in range(M):
        p = await asyncio.to_thread(store.create_project, {"name": f"ragproj-{i}", "rag_mode": "AUTO"})
        pids.append(p["id"])

    async def _query_once(pid: str) -> float:
        t = time.monotonic()
        try:
            await rc.query(pid, "test query", top_k=3)
        except Exception as e:
            # upstream may reject; we care about timing not result correctness
            pass
        return time.monotonic() - t

    # warm up kb creation (first query per project creates the kb)
    await asyncio.gather(*[_query_once(pid) for pid in pids])

    # timed concurrent round
    t0 = time.monotonic()
    durations = await asyncio.gather(*[_query_once(pid) for pid in pids])
    wall = time.monotonic() - t0

    await gc.close()
    store.close()

    total_serial = sum(durations)
    speedup = total_serial / wall if wall > 0 else 0
    print(f"\n[M6 stress] M={M} projects concurrent: wall={wall:.2f}s "
          f"sum_of_calls={total_serial:.2f}s speedup={speedup:.2f}x")
    # if serialized behind one lock, wall ~= total_serial (speedup ~1x).
    # concurrent should give speedup > 1.5x (allow upstream jitter).
    assert speedup > 1.5, (
        f"RAG queries serialized: wall={wall:.2f}s vs sum={total_serial:.2f}s "
        f"(speedup {speedup:.2f}x) — global kb lock regression"
    )
