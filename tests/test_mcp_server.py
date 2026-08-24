import json
from unittest.mock import AsyncMock

import pytest

from project_service import config
from project_service.daemon_server import ProjectRPCServer
from project_service.engine.agent_binder import AgentBinder, AgentUnavailable
from project_service.engine.chat_manager import ChatManager
from project_service.engine.gateway_client import GatewayClient, GatewayError
from project_service.engine.instruction_engine import InstructionEngine
from project_service.engine.knowledge_manager import KnowledgeManager
from project_service.engine.project_manager import ProjectManager
from project_service.mcp_server import MCPServer
from project_service.store.file_store import FileStore
from project_service.store.project_store import ProjectStore
from project_service.models.project import ProjectCreate


# ── fixtures ──


@pytest.fixture
def rpc(tmp_path):
    store = ProjectStore(db_path=tmp_path / "projects.db")
    fs = FileStore(storage_dir=tmp_path / "storage")
    pm = ProjectManager(store=store, file_store=fs)
    ie = InstructionEngine(store=store, project_manager=pm)
    cm = ChatManager(store=store, project_manager=pm, file_store=fs)
    km = KnowledgeManager(store=store, project_manager=pm)
    fake_upstream = AsyncMock(spec=GatewayClient)
    fake_upstream.agent_list = AsyncMock(return_value=[])
    fake_upstream.agent_get = AsyncMock(return_value=None)
    ab = AgentBinder(store=store, project_manager=pm, upstream=fake_upstream)
    server = ProjectRPCServer(
        project_manager=pm,
        instruction_engine=ie,
        chat_manager=cm,
        knowledge_manager=km,
        agent_binder=ab,
        upstream=fake_upstream,
    )
    yield server
    store.close()


@pytest.fixture
def mcp(rpc):
    return MCPServer(rpc_server=rpc)


# ── R4: dead agent_execute method removed ──


def test_agent_execute_method_removed():
    assert not hasattr(GatewayClient, "agent_execute"), "R4 dead code still present"


# ── unknown tool maps to -32601 ──


@pytest.mark.asyncio
async def test_mcp_unknown_tool(mcp):
    raw = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "no_such_tool", "arguments": {}},
    }).encode("utf-8")
    resp = json.loads((await mcp.handle_request(raw)).decode("utf-8"))
    assert resp["error"]["code"] == -32601


# ── Fix 3: domain exception mapping (H2 drift point 3) ──


@pytest.mark.asyncio
async def test_mcp_project_not_found_mapped(mcp):
    raw = json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "project_get", "arguments": {"project_id": "nope"}},
    }).encode("utf-8")
    resp = json.loads((await mcp.handle_request(raw)).decode("utf-8"))
    assert resp["result"]["isError"] is True
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert "project not found" in payload["error"]


@pytest.mark.asyncio
async def test_mcp_gateway_error_mapped(mcp, rpc):
    async def _fail(project_id):
        raise GatewayError("agent-studio down: " + project_id)
    rpc.project_manager.get = _fail
    raw = json.dumps({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "project_get", "arguments": {"project_id": "p1"}},
    }).encode("utf-8")
    resp = json.loads((await mcp.handle_request(raw)).decode("utf-8"))
    assert resp["result"]["isError"] is True
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert "gateway error" in payload["error"]


# ── Fix 1: H6-item4 set_binding rejects agent not found upstream ──


@pytest.mark.asyncio
async def test_set_binding_rejects_missing_agent(rpc):
    rpc.agent_binder.upstream.agent_get = AsyncMock(return_value=None)
    proj = await rpc.project_manager.create(ProjectCreate(name="mcp-bind"))
    with pytest.raises(AgentUnavailable):
        await rpc.agent_binder.set_binding(proj.id, agent_id="ghost-agent")


@pytest.mark.asyncio
async def test_set_binding_allows_known_agent(rpc):
    rpc.agent_binder.upstream.agent_get = AsyncMock(return_value={
        "id": "agent-1", "name": "Coder", "description": "x", "avatar": None,
        "tools": ["read"], "rag_enabled": True, "permissions": [],
    })
    proj = await rpc.project_manager.create(ProjectCreate(name="mcp-bind-ok"))
    binding = await rpc.agent_binder.set_binding(proj.id, agent_id="agent-1")
    assert binding.agent_id == "agent-1"


# ── Fix 4: MCP stdio line byte cap (R7 attack surface) ──


def test_mcp_max_line_bytes_config_exists():
    assert config.MCP_MAX_LINE_BYTES > 0
    assert config.MCP_MAX_LINE_BYTES == config.UDS_MAX_LINE_BYTES


def test_mcp_stdio_loop_has_cap():
    import inspect
    from project_service.mcp_server import run_mcp_stdio
    src = inspect.getsource(run_mcp_stdio)
    assert "MCP_MAX_LINE_BYTES" in src, "R7: stdio loop missing byte cap"
    assert "request too large" in src, "R7: stdio loop missing oversize rejection"
