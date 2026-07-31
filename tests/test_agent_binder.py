import pytest

from project_service.engine.agent_binder import AgentBinder, AgentBinderError
from project_service.engine.chat_manager import ChatManager
from project_service.engine.instruction_engine import InstructionEngine
from project_service.engine.project_manager import ProjectManager
from project_service.models.agent_binding import PromptMergeMode
from project_service.models.chat import ChatCreate
from project_service.models.instruction import InstructionSave
from project_service.models.project import ProjectCreate
from project_service.store.project_store import ProjectStore


class FakeUpstream:
    async def agent_list(self):
        return [
            {"id": "agent-1", "name": "Coder", "description": "code helper", "avatar": None},
        ]

    async def agent_get(self, agent_id):
        if agent_id == "agent-1":
            return {
                "id": "agent-1",
                "name": "Coder",
                "description": "code helper",
                "avatar": None,
                "tools": ["read", "write"],
                "rag_enabled": True,
                "permissions": ["network"],
            }
        return None


@pytest.fixture
def binder(tmp_path):
    store = ProjectStore(db_path=tmp_path / "projects.db")
    pm = ProjectManager(store=store)
    upstream = FakeUpstream()
    mgr = AgentBinder(store=store, project_manager=pm, upstream=upstream)
    yield mgr
    store.close()


@pytest.fixture
async def project_id(binder):
    proj = await binder.project_manager.create(ProjectCreate(name="abtest"))
    return proj.id


async def _create_chat(store, project_id):
    cm = ChatManager(store=store, project_manager=store._pm if hasattr(store, "_pm") else None)
    return None


@pytest.mark.asyncio
async def test_set_and_get_project_binding(binder, project_id):
    binding = await binder.set_binding(project_id, agent_id="agent-1")
    assert binding.agent_id == "agent-1"
    assert binding.project_id == project_id
    got = await binder.get_binding(project_id)
    assert got.agent_id == "agent-1"
    assert got.chat_id is None


@pytest.mark.asyncio
async def test_set_chat_level_binding(binder, project_id):
    await binder.set_binding(project_id, agent_id="agent-1")
    cm = ChatManager(store=binder.store, project_manager=binder.project_manager)
    chat = await cm.create_chat(project_id, ChatCreate(title="test-chat"))
    chat_binding = await binder.set_binding(
        project_id, agent_id="agent-1", chat_id=chat.id
    )
    assert chat_binding.chat_id == chat.id
    got = await binder.get_binding(project_id, chat_id=chat.id)
    assert got.agent_id == "agent-1"
    assert got.chat_id == chat.id


@pytest.mark.asyncio
async def test_get_binding_fallback_to_project(binder, project_id):
    await binder.set_binding(project_id, agent_id="agent-1")
    got = await binder.get_binding(project_id, chat_id="unknown-chat")
    assert got.agent_id == "agent-1"
    assert got.chat_id is None


@pytest.mark.asyncio
async def test_remove_binding(binder, project_id):
    await binder.set_binding(project_id, agent_id="agent-1")
    await binder.remove_binding(project_id)
    got = await binder.get_binding(project_id)
    assert got.agent_id is None


@pytest.mark.asyncio
async def test_remove_chat_binding(binder, project_id):
    await binder.set_binding(project_id, agent_id="agent-1")
    cm = ChatManager(store=binder.store, project_manager=binder.project_manager)
    chat = await cm.create_chat(project_id, ChatCreate(title="test-chat"))
    await binder.set_binding(project_id, agent_id="agent-2", chat_id=chat.id)
    await binder.remove_binding(project_id, chat_id=chat.id)
    got = await binder.get_binding(project_id, chat_id=chat.id)
    assert got.agent_id == "agent-1"


@pytest.mark.asyncio
async def test_list_available_agents(binder):
    agents = await binder.list_available_agents()
    assert len(agents) == 1
    assert agents[0].name == "Coder"


@pytest.mark.asyncio
async def test_get_agent_preview(binder):
    preview = await binder.get_agent_preview("agent-1")
    assert preview is not None
    assert preview.name == "Coder"
    assert "read" in preview.tools


@pytest.mark.asyncio
async def test_build_system_prompt_agent_first(binder, project_id):
    await binder.set_binding(
        project_id, agent_id="agent-1", merge_mode=PromptMergeMode.AGENT_FIRST
    )
    prompt = await binder.build_system_prompt(
        project_id, agent_prompt="You are a coder."
    )
    assert "You are a coder." in prompt


@pytest.mark.asyncio
async def test_build_system_prompt_project_only(binder, project_id):
    ie = InstructionEngine(store=binder.store, project_manager=binder.project_manager)
    await ie.save(project_id, InstructionSave(content="Project instructions only"))
    await binder.set_binding(
        project_id, agent_id="agent-1", merge_mode=PromptMergeMode.PROJECT_ONLY
    )
    prompt = await binder.build_system_prompt(
        project_id, agent_prompt="Should be ignored"
    )
    assert "Project instructions only" in prompt
    assert "Should be ignored" not in prompt
