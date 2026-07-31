import asyncio
import os
import uuid

import pytest

from project_service.client import ProjectClient, RPCError
from project_service.daemon_server import ProjectRPCServer
from project_service.engine.instruction_engine import InstructionEngine
from project_service.engine.project_manager import ProjectManager
from project_service.store.file_store import FileStore
from project_service.store.project_store import ProjectStore


@pytest.fixture
def managers(tmp_path):
    store = ProjectStore(db_path=tmp_path / "p.db")
    fs = FileStore(storage_dir=tmp_path / "storage")
    pm = ProjectManager(store=store, file_store=fs)
    ie = InstructionEngine(store=store, project_manager=pm)
    return pm, ie


@pytest.fixture
async def rpc_server(managers, tmp_path):
    pm, ie = managers
    srv = ProjectRPCServer(project_manager=pm, instruction_engine=ie)
    sock = f"/tmp/fp-svc-test-{uuid.uuid4().hex}.sock"
    task = asyncio.create_task(srv.serve(sock))
    for _ in range(100):
        if os.path.exists(sock):
            break
        await asyncio.sleep(0.01)
    yield sock
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    if os.path.exists(sock):
        os.remove(sock)


async def test_rpc_project_lifecycle(rpc_server):
    client = ProjectClient(sock_path=rpc_server)
    created = await client.create_project(name="A", description="d")
    assert created["name"] == "A"
    pid = created["id"]

    assert (await client.get_project(pid))["id"] == pid
    assert len(await client.list_projects()) == 1

    upd = await client.update_project(pid, name="A2")
    assert upd["name"] == "A2"

    starred = await client.star_project(pid, True)
    assert starred["is_starred"] is True

    with pytest.raises(RPCError):
        await client.delete_project(pid)

    await client.archive_project(pid)
    await client.delete_project(pid)
    with pytest.raises(RPCError):
        await client.get_project(pid)


async def test_rpc_instruction_flow(rpc_server):
    client = ProjectClient(sock_path=rpc_server)
    p = await client.create_project(name="B")
    pid = p["id"]

    assert (await client.get_instruction(pid))["content"] == ""
    saved = await client.save_instruction(pid, "be helpful")
    assert saved["content"] == "be helpful"

    await client.save_instruction(pid, "be very helpful")
    snaps = await client.list_instruction_snapshots(pid)
    assert len(snaps) == 1
    assert snaps[0]["content"] == "be helpful"

    await client.clear_instruction(pid)
    assert (await client.get_instruction(pid))["content"] == ""


async def test_rpc_unknown_method(rpc_server):
    client = ProjectClient(sock_path=rpc_server)
    with pytest.raises(RPCError):
        await client.call("project.bogus")
