import pytest
import httpx

from project_service.api.rest_server import create_app
from project_service.engine.instruction_engine import InstructionEngine
from project_service.engine.project_manager import ProjectManager
from project_service.store.file_store import FileStore
from project_service.store.project_store import ProjectStore


@pytest.fixture
async def client(tmp_path):
    store = ProjectStore(db_path=tmp_path / "p.db")
    fs = FileStore(storage_dir=tmp_path / "storage")
    pm = ProjectManager(store=store, file_store=fs)
    ie = InstructionEngine(store=store, project_manager=pm)
    app = create_app(project_manager=pm, instruction_engine=ie)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client):
    r = await client.get("/health")
    assert r.json()["status"] == "ok"


async def test_project_lifecycle(client):
    r = await client.post("/v1/projects", json={"name": "A", "description": "d"})
    assert r.status_code == 201
    pid = r.json()["id"]

    assert (await client.get(f"/v1/projects/{pid}")).json()["name"] == "A"
    assert len((await client.get("/v1/projects")).json()) == 1
    assert (await client.patch(f"/v1/projects/{pid}", json={"name": "A2"})).json()["name"] == "A2"
    assert (await client.post(f"/v1/projects/{pid}/star")).json()["is_starred"] is True

    assert (await client.delete(f"/v1/projects/{pid}")).status_code == 409
    await client.post(f"/v1/projects/{pid}/archive")
    assert (await client.delete(f"/v1/projects/{pid}")).status_code == 204
    assert (await client.get(f"/v1/projects/{pid}")).status_code == 404


async def test_instruction_flow(client):
    pid = (await client.post("/v1/projects", json={"name": "B"})).json()["id"]

    assert (await client.get(f"/v1/projects/{pid}/instructions")).json()["content"] == ""
    assert (
        await client.put(f"/v1/projects/{pid}/instructions", json={"content": "hi"})
    ).json()["content"] == "hi"
    await client.put(f"/v1/projects/{pid}/instructions", json={"content": "hi2"})
    assert len(
        (await client.get(f"/v1/projects/{pid}/instructions/snapshots")).json()
    ) == 1
    assert (
        await client.delete(f"/v1/projects/{pid}/instructions")
    ).status_code == 204
    assert (
        await client.get(f"/v1/projects/{pid}/instructions")
    ).json()["content"] == ""
