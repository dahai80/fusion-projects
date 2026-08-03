import json
from unittest.mock import AsyncMock

import pytest

from project_service.daemon_server import ProjectRPCServer
from project_service.engine.chat_manager import ChatManager
from project_service.engine.gateway_client import GatewayClient
from project_service.engine.instruction_engine import InstructionEngine
from project_service.engine.knowledge_manager import KnowledgeManager
from project_service.engine.project_manager import ProjectManager
from project_service.store.project_store import ProjectStore


@pytest.fixture
def rpc(tmp_path):
    store = ProjectStore(db_path=tmp_path / "projects.db")
    pm = ProjectManager(store=store)
    ie = InstructionEngine(store=store, project_manager=pm)
    cm = ChatManager(store=store, project_manager=pm)
    km = KnowledgeManager(store=store, project_manager=pm)

    fake_upstream = AsyncMock(spec=GatewayClient)
    fake_upstream.agent_list = AsyncMock(return_value=[
        {"id": "agent-1", "name": "Coder", "description": "code helper", "avatar": None},
    ])
    fake_upstream.agent_get = AsyncMock(return_value={
        "id": "agent-1", "name": "Coder", "description": "code helper",
        "avatar": None, "tools": ["read"], "rag_enabled": True, "permissions": ["network"],
    })

    server = ProjectRPCServer(
        project_manager=pm,
        instruction_engine=ie,
        chat_manager=cm,
        knowledge_manager=km,
        upstream=fake_upstream,
    )
    yield server
    store.close()


def _req(method: str, params: dict | None = None, req_id: int = 1) -> bytes:
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params:
        msg["params"] = params
    return json.dumps(msg).encode("utf-8")


def _parse(resp: bytes) -> dict:
    return json.loads(resp.decode("utf-8"))


@pytest.mark.asyncio
async def test_dispatch_project_create_and_list(rpc):
    result = await rpc.dispatch("project.create", {"name": "rpc-proj"})
    assert result["name"] == "rpc-proj"
    project_id = result["id"]
    items = await rpc.dispatch("project.list", {})
    assert len(items) == 1
    assert items[0]["id"] == project_id


@pytest.mark.asyncio
async def test_dispatch_project_star(rpc):
    created = await rpc.dispatch("project.create", {"name": "star-proj"})
    pid = created["id"]
    starred = await rpc.dispatch(
        "project.star", {"project_id": pid, "starred": True}
    )
    assert starred["is_starred"] is True


@pytest.mark.asyncio
async def test_dispatch_chat_create_and_list(rpc):
    proj = await rpc.dispatch("project.create", {"name": "chat-proj"})
    pid = proj["id"]
    chat = await rpc.dispatch(
        "project.chat.create", {"project_id": pid, "title": "hello"}
    )
    assert chat["title"] == "hello"
    chats = await rpc.dispatch("project.chat.list", {"project_id": pid})
    assert len(chats) == 1


@pytest.mark.asyncio
async def test_dispatch_chat_fork(rpc):
    proj = await rpc.dispatch("project.create", {"name": "fork-proj"})
    pid = proj["id"]
    chat = await rpc.dispatch(
        "project.chat.create", {"project_id": pid, "title": "src"}
    )
    cid = chat["id"]
    await rpc.dispatch(
        "project.chat.message.add",
        {"chat_id": cid, "content": "msg1"},
    )
    forked = await rpc.dispatch(
        "project.chat.fork", {"chat_id": cid, "label": "fork-1"}
    )
    assert forked["fork_from_chat_id"] == cid


@pytest.mark.asyncio
async def test_dispatch_knowledge_folder(rpc):
    proj = await rpc.dispatch("project.create", {"name": "k-proj"})
    pid = proj["id"]
    folder = await rpc.dispatch(
        "project.knowledge.folder.create", {"project_id": pid, "name": "docs"}
    )
    assert folder["name"] == "docs"
    folders = await rpc.dispatch(
        "project.knowledge.folder.list", {"project_id": pid}
    )
    assert len(folders) == 1


@pytest.mark.asyncio
async def test_dispatch_agent_set_and_get(rpc):
    proj = await rpc.dispatch("project.create", {"name": "ag-proj"})
    pid = proj["id"]
    binding = await rpc.dispatch(
        "project.agent.set",
        {"project_id": pid, "agent_id": "agent-1", "merge_mode": "AGENT_FIRST"},
    )
    assert binding["agent_id"] == "agent-1"
    got = await rpc.dispatch("project.agent.get", {"project_id": pid})
    assert got["agent_id"] == "agent-1"


@pytest.mark.asyncio
async def test_handle_request_parse_error(rpc):
    resp = await rpc.handle_request(b"not-json{{{")
    parsed = _parse(resp)
    assert parsed["error"]["code"] == -32700


@pytest.mark.asyncio
async def test_handle_request_method_not_found(rpc):
    resp = await rpc.handle_request(_req("project.nonexistent"))
    parsed = _parse(resp)
    assert parsed["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_handle_request_invalid_params(rpc):
    resp = await rpc.handle_request(
        _req("project.create", {"name": ""})
    )
    parsed = _parse(resp)
    assert parsed["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_handle_request_chat_not_found(rpc):
    resp = await rpc.handle_request(
        _req("project.chat.get", {"chat_id": "nonexistent"})
    )
    parsed = _parse(resp)
    assert parsed["error"]["code"] == -32005


@pytest.mark.asyncio
async def test_instruction_snapshot_restore_and_delete(rpc):
    proj = await rpc.dispatch("project.create", {"name": "snap-proj"})
    pid = proj["id"]
    await rpc.dispatch("project.instruction.save", {"project_id": pid, "content": "v1"})
    await rpc.dispatch("project.instruction.save", {"project_id": pid, "content": "v2"})
    snaps = await rpc.dispatch("project.instruction.snapshots", {"project_id": pid})
    assert len(snaps) >= 1
    snap_id = snaps[0]["id"]
    restored = await rpc.dispatch(
        "project.instruction.snapshot.restore", {"snapshot_id": snap_id}
    )
    assert restored["content"] == snaps[0]["content"]
    deleted = await rpc.dispatch(
        "project.instruction.snapshot.delete", {"snapshot_id": snap_id}
    )
    assert deleted["deleted"] is True


@pytest.mark.asyncio
async def test_instruction_snapshot_not_found(rpc):
    resp = await rpc.handle_request(
        _req("project.instruction.snapshot.restore", {"snapshot_id": "bad"})
    )
    parsed = _parse(resp)
    assert parsed["error"]["code"] == -32010


@pytest.mark.asyncio
async def test_project_create_with_instructions(rpc):
    proj = await rpc.dispatch(
        "project.create", {"name": "instr-proj", "instructions": "be helpful"}
    )
    pid = proj["id"]
    ic = await rpc.dispatch("project.instruction.get", {"project_id": pid})
    assert ic["content"] == "be helpful"


@pytest.mark.asyncio
async def test_chat_move(rpc):
    proj1 = await rpc.dispatch("project.create", {"name": "src-proj"})
    proj2 = await rpc.dispatch("project.create", {"name": "dst-proj"})
    pid1, pid2 = proj1["id"], proj2["id"]
    chat = await rpc.dispatch(
        "project.chat.create", {"project_id": pid1, "title": "move-me"}
    )
    cid = chat["id"]
    moved = await rpc.dispatch(
        "project.chat.move", {"chat_id": cid, "target_project_id": pid2}
    )
    assert moved["project_id"] == pid2


@pytest.mark.asyncio
async def test_chat_detach(rpc):
    proj = await rpc.dispatch("project.create", {"name": "detach-proj"})
    pid = proj["id"]
    chat = await rpc.dispatch(
        "project.chat.create", {"project_id": pid, "title": "detach-me"}
    )
    cid = chat["id"]
    detached = await rpc.dispatch("project.chat.detach", {"chat_id": cid})
    assert detached["project_id"] is None


@pytest.mark.asyncio
async def test_temp_attachments(rpc):
    proj = await rpc.dispatch("project.create", {"name": "ta-proj"})
    pid = proj["id"]
    chat = await rpc.dispatch(
        "project.chat.create", {"project_id": pid, "title": "ta-chat"}
    )
    cid = chat["id"]
    ta = await rpc.dispatch(
        "project.chat.temp_attachment.add",
        {"chat_id": cid, "file_path": "/tmp/test.pdf", "original_name": "test.pdf", "file_size": 1024},
    )
    assert ta["original_name"] == "test.pdf"
    tas = await rpc.dispatch(
        "project.chat.temp_attachment.list", {"chat_id": cid}
    )
    assert len(tas) == 1
    deleted = await rpc.dispatch(
        "project.chat.temp_attachment.delete", {"attachment_id": ta["id"]}
    )
    assert deleted["deleted"] is True


@pytest.mark.asyncio
async def test_project_duplicate(rpc):
    proj = await rpc.dispatch("project.create", {"name": "orig", "instructions": "be smart"})
    pid = proj["id"]
    dup = await rpc.dispatch("project.duplicate", {"project_id": pid, "name": "dup"})
    assert dup["name"] == "dup"
    assert dup["id"] != pid
    ic = await rpc.dispatch("project.instruction.get", {"project_id": dup["id"]})
    assert ic["content"] == "be smart"


@pytest.mark.asyncio
async def test_audit_log(rpc):
    proj = await rpc.dispatch("project.create", {"name": "audit-proj"})
    pid = proj["id"]
    entry = await rpc.dispatch(
        "project.audit.log",
        {"project_id": pid, "action": "chat.created", "details": "test"},
    )
    assert entry["action"] == "chat.created"
    entries = await rpc.dispatch("project.audit.list", {"project_id": pid})
    assert len(entries) >= 1


@pytest.mark.asyncio
async def test_rag_config_get_and_set(rpc):
    proj = await rpc.dispatch("project.create", {"name": "rag-cfg"})
    pid = proj["id"]
    cfg = await rpc.dispatch("project.rag.config.get", {"project_id": pid})
    assert "rag_mode" in cfg
    updated = await rpc.dispatch(
        "project.rag.config.set",
        {"project_id": pid, "rag_mode": "HYBRID", "rag_top_k": 10},
    )
    assert updated["rag_mode"] == "HYBRID"
    assert updated["rag_top_k"] == 10


@pytest.mark.asyncio
async def test_mcp_initialize():
    from project_service.mcp_server import MCPServer
    mcp = MCPServer(rpc_server=None)
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}).encode()
    resp = await mcp.handle_request(req)
    parsed = json.loads(resp.decode())
    assert parsed["result"]["serverInfo"]["name"] == "fusion-project-mcp"


@pytest.mark.asyncio
async def test_mcp_tools_list():
    from project_service.mcp_server import MCPServer
    mcp = MCPServer(rpc_server=None)
    req = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode()
    resp = await mcp.handle_request(req)
    parsed = json.loads(resp.decode())
    tool_names = [t["name"] for t in parsed["result"]["tools"]]
    assert "project_list" in tool_names
    assert "project_get" in tool_names
    assert "project_search_knowledge" in tool_names


@pytest.mark.asyncio
async def test_mcp_unknown_method():
    from project_service.mcp_server import MCPServer
    mcp = MCPServer(rpc_server=None)
    req = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "unknown/method"}).encode()
    resp = await mcp.handle_request(req)
    parsed = json.loads(resp.decode())
    assert parsed["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_mcp_parse_error():
    from project_service.mcp_server import MCPServer
    mcp = MCPServer(rpc_server=None)
    resp = await mcp.handle_request(b"bad{json")
    parsed = json.loads(resp.decode())
    assert parsed["error"]["code"] == -32700


@pytest.mark.asyncio
async def test_cowork_relay_methods_exist(rpc):
    resp = await rpc.handle_request(
        _req("cowork.trigger", {"project_id": "x", "action": "test"})
    )
    parsed = _parse(resp)
    assert "result" in parsed
    assert parsed["result"]["error"] == "cowork_unavailable"

    resp = await rpc.handle_request(
        _req("cowork.status", {"task_id": "nonexistent"})
    )
    parsed = _parse(resp)
    assert "result" in parsed
    assert parsed["result"]["error"] == "cowork_unavailable"


@pytest.mark.asyncio
async def test_project_export(rpc):
    proj = await rpc.dispatch("project.create", {"name": "export-proj", "instructions": "export test"})
    pid = proj["id"]
    result = await rpc.dispatch("project.export", {"project_id": pid})
    assert "zip_base64" in result
    assert result["size"] > 0
    import base64
    import zipfile
    import io
    zdata = base64.b64decode(result["zip_base64"])
    zf = zipfile.ZipFile(io.BytesIO(zdata))
    names = zf.namelist()
    assert "project.json" in names
    assert "instructions.json" in names
