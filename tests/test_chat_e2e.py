import os
import tempfile

import pytest

pytestmark = pytest.mark.integration

FUSION_GATEWAY_API_KEY = os.environ.get("FUSION_GATEWAY_API_KEY", "fg-demo-key-change-me")
# fusion-mlx settings.json auth.api_key is "dahai168". The shell env may carry a
# DIFFERENT FUSION_MLX_API_KEY (set by other fusion services) so we only honor it
# when the test explicitly overrides via FUSION_E2E_MLX_API_KEY.
FUSION_MLX_API_KEY = os.environ.get("FUSION_E2E_MLX_API_KEY", "dahai168")

INSTR_KEYWORD = "PINEAPPLE"
KB_FACT = "The secret launch code is 7Z9-Q4K."


def _upstreams_up() -> bool:
    import httpx
    checks = [
        ("http://127.0.0.1:11432/health", "fg-demo-key-change-me"),
        ("http://127.0.0.1:11434/health", FUSION_MLX_API_KEY),
        ("http://127.0.0.1:11436/health", None),
    ]
    try:
        with httpx.Client(timeout=3.0) as c:
            for url, key in checks:
                headers = {"Authorization": f"Bearer {key}"} if key else {}
                r = c.get(url, headers=headers)
                if r.status_code != 200:
                    return False
        return True
    except Exception:
        return False


skip_no_upstream = pytest.mark.skipif(
    not _upstreams_up(),
    reason="upstreams not all up (gateway 11432 / mlx 11434 / rag 11436)",
)


@pytest.fixture
async def e2e_server(tmp_path):
    # LLM goes direct to fusion-mlx (11434) so Qwen3-0.6B-4bit runs locally
    # (avoids gateway cloud-routing 502; still a real loaded model, no mock).
    # config.* are bound at import time, so set the module attrs directly
    # before constructing GatewayClient (which reads them in __init__).
    from project_service import config

    config.GATEWAY_URL = "http://127.0.0.1:11434"
    config.GATEWAY_API_KEY = FUSION_MLX_API_KEY
    config.RAG_BASE_URL = "http://127.0.0.1:11436"
    config.AGENT_STUDIO_URL = "http://127.0.0.1:11455"

    from project_service.store.project_store import ProjectStore
    from project_service.store.file_store import FileStore
    from project_service.daemon_server import ProjectRPCServer
    from project_service.engine.project_manager import ProjectManager
    from project_service.engine.instruction_engine import InstructionEngine
    from project_service.engine.chat_manager import ChatManager
    from project_service.engine.knowledge_manager import KnowledgeManager
    from project_service.engine.agent_binder import AgentBinder
    from project_service.engine.rag_coordinator import RAGCoordinator
    from project_service.engine.gateway_client import GatewayClient

    store = ProjectStore(db_path=os.path.join(str(tmp_path), "projects.db"))
    fs = FileStore(storage_dir=os.path.join(str(tmp_path), "storage"))
    gc = GatewayClient()
    pm = ProjectManager(store=store, file_store=fs, upstream=gc)
    ie = InstructionEngine(store=store, project_manager=pm)
    cm = ChatManager(store=store, project_manager=pm)
    ab = AgentBinder(store=store, project_manager=pm, upstream=gc)
    rc = RAGCoordinator(store=store, project_manager=pm, upstream=gc)
    pm.rag_coordinator = rc
    km = KnowledgeManager(store=store, project_manager=pm, rag_coordinator=rc)

    server = ProjectRPCServer()
    server.project_manager = pm
    server.instruction_engine = ie
    server.chat_manager = cm
    server.knowledge_manager = km
    server.agent_binder = ab
    server.rag_coordinator = rc
    server.gateway_client = gc

    yield server
    await gc.close()
    store.close()


@skip_no_upstream
@pytest.mark.asyncio
async def test_e2e_instruction_injected(e2e_server):
    proj = await e2e_server.dispatch("project.create", {
        "name": "e2e-instr",
        "instructions": f"You MUST begin every reply with the word {INSTR_KEYWORD}.",
    })
    pid = proj["id"]
    chat = await e2e_server.dispatch("project.chat.create", {"project_id": pid, "title": "instr"})
    cid = chat["id"]
    result = await e2e_server.dispatch("project.chat.message.stream", {
        "chat_id": cid,
        "content": "Say hello.",
        "rag_mode": "OFF",
        "model": "Qwen3-0.6B-4bit",
        "max_tokens": 80,
    })
    content = result["message"]["content"]
    assert content.strip(), "empty reply"
    assert INSTR_KEYWORD in content, f"instruction keyword missing: {content!r}"


@skip_no_upstream
@pytest.mark.asyncio
async def test_e2e_rag_knowledge_injected(e2e_server, tmp_path):
    proj = await e2e_server.dispatch("project.create", {"name": "e2e-rag", "rag_mode": "AUTO"})
    pid = proj["id"]
    src = tmp_path / "secret.md"
    src.write_text(KB_FACT)
    kfile = await e2e_server.dispatch("project.knowledge.file.upload", {
        "project_id": pid,
        "source_path": str(src),
        "original_name": "secret.md",
    })
    assert kfile["index_status"] == "INDEXED", f"auto-index failed: {kfile['index_status']}"
    chat = await e2e_server.dispatch("project.chat.create", {"project_id": pid, "title": "rag"})
    cid = chat["id"]
    result = await e2e_server.dispatch("project.chat.message.stream", {
        "chat_id": cid,
        "content": "What is the secret launch code? Answer using only the provided reference.",
        "rag_mode": "AUTO",
        "model": "Qwen3-0.6B-4bit",
        "max_tokens": 120,
    })
    content = result["message"]["content"]
    assert content.strip(), "empty reply"
    assert "7Z9-Q4K" in content, f"kb fact missing from reply: {content!r}"


@skip_no_upstream
@pytest.mark.asyncio
async def test_e2e_instruction_plus_rag(e2e_server, tmp_path):
    proj = await e2e_server.dispatch("project.create", {
        "name": "e2e-both",
        "instructions": f"You MUST begin every reply with the word {INSTR_KEYWORD}.",
        "rag_mode": "AUTO",
    })
    pid = proj["id"]
    src = tmp_path / "both.md"
    src.write_text(KB_FACT)
    kfile = await e2e_server.dispatch("project.knowledge.file.upload", {
        "project_id": pid,
        "source_path": str(src),
        "original_name": "both.md",
    })
    assert kfile["index_status"] == "INDEXED"
    chat = await e2e_server.dispatch("project.chat.create", {"project_id": pid, "title": "both"})
    cid = chat["id"]
    result = await e2e_server.dispatch("project.chat.message.stream", {
        "chat_id": cid,
        "content": "What is the secret launch code? Answer using only the provided reference.",
        "rag_mode": "AUTO",
        "model": "Qwen3-0.6B-4bit",
        "max_tokens": 120,
    })
    content = result["message"]["content"]
    assert INSTR_KEYWORD in content, f"instruction keyword missing: {content!r}"
    assert "7Z9-Q4K" in content, f"kb fact missing: {content!r}"


@skip_no_upstream
@pytest.mark.asyncio
async def test_e2e_rag_mode_off_skips_knowledge(e2e_server, tmp_path):
    proj = await e2e_server.dispatch("project.create", {"name": "e2e-off", "rag_mode": "OFF"})
    pid = proj["id"]
    src = tmp_path / "hidden.md"
    src.write_text(KB_FACT)
    await e2e_server.dispatch("project.knowledge.file.upload", {
        "project_id": pid,
        "source_path": str(src),
        "original_name": "hidden.md",
    })
    chat = await e2e_server.dispatch("project.chat.create", {"project_id": pid, "title": "off"})
    cid = chat["id"]
    result = await e2e_server.dispatch("project.chat.message.stream", {
        "chat_id": cid,
        "content": "What is the secret launch code?",
        "rag_mode": "OFF",
        "model": "Qwen3-0.6B-4bit",
        "max_tokens": 120,
    })
    content = result["message"]["content"]
    assert content.strip(), "empty reply"
    assert "7Z9-Q4K" not in content, f"rag_mode=OFF leaked kb fact: {content!r}"


@skip_no_upstream
@pytest.mark.asyncio
async def test_e2e_history_limit_applied(e2e_server, monkeypatch):
    from project_service import config
    monkeypatch.setattr(config, "CHAT_HISTORY_LIMIT", 2)
    proj = await e2e_server.dispatch("project.create", {"name": "e2e-hist"})
    pid = proj["id"]
    chat = await e2e_server.dispatch("project.chat.create", {"project_id": pid, "title": "hist"})
    cid = chat["id"]
    for word in ("ALPHA", "BETA", "GAMMA", "DELTA"):
        await e2e_server.dispatch("project.chat.message.add", {
            "chat_id": cid,
            "content": f"remember the word {word}",
            "role": "user",
        })
    result = await e2e_server.dispatch("project.chat.message.stream", {
        "chat_id": cid,
        "content": "Which words did I ask you to remember? List them.",
        "rag_mode": "OFF",
        "model": "Qwen3-0.6B-4bit",
        "max_tokens": 150,
    })
    content = result["message"]["content"]
    assert "DELTA" in content, f"recent msg missing: {content!r}"
    assert "ALPHA" not in content, f"truncated msg leaked (limit=2): {content!r}"
