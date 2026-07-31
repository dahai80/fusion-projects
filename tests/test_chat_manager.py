import pytest

from project_service.engine.chat_manager import ChatManager, ChatNotFound
from project_service.engine.project_manager import ProjectManager
from project_service.models.chat import ChatCreate, ChatUpdate, MessageCreate
from project_service.models.project import ProjectCreate
from project_service.store.project_store import ProjectStore


@pytest.fixture
def chat_mgr(tmp_path):
    store = ProjectStore(db_path=tmp_path / "projects.db")
    pm = ProjectManager(store=store)
    mgr = ChatManager(store=store, project_manager=pm)
    yield mgr
    store.close()


@pytest.fixture
async def project_id(chat_mgr):
    proj = await chat_mgr.project_manager.create(ProjectCreate(name="test-proj"))
    return proj.id


@pytest.mark.asyncio
async def test_create_and_get(chat_mgr, project_id):
    chat = await chat_mgr.create_chat(project_id, ChatCreate(title="hello"))
    assert chat.title == "hello"
    assert chat.project_id == project_id
    got = await chat_mgr.get_chat(chat.id)
    assert got.id == chat.id


@pytest.mark.asyncio
async def test_list_chats(chat_mgr, project_id):
    await chat_mgr.create_chat(project_id, ChatCreate(title="c1"))
    await chat_mgr.create_chat(project_id, ChatCreate(title="c2"))
    chats = await chat_mgr.list_chats(project_id)
    assert len(chats) == 2


@pytest.mark.asyncio
async def test_update_chat(chat_mgr, project_id):
    chat = await chat_mgr.create_chat(project_id, ChatCreate(title="old"))
    updated = await chat_mgr.update_chat(chat.id, {"title": "new"})
    assert updated.title == "new"


@pytest.mark.asyncio
async def test_star_chat(chat_mgr, project_id):
    chat = await chat_mgr.create_chat(project_id, ChatCreate(title="s"))
    starred = await chat_mgr.star_chat(chat.id, True)
    assert starred.is_starred is True
    unstarred = await chat_mgr.star_chat(chat.id, False)
    assert unstarred.is_starred is False


@pytest.mark.asyncio
async def test_delete_chat(chat_mgr, project_id):
    chat = await chat_mgr.create_chat(project_id, ChatCreate(title="del"))
    await chat_mgr.delete_chat(chat.id)
    with pytest.raises(ChatNotFound):
        await chat_mgr.get_chat(chat.id)


@pytest.mark.asyncio
async def test_add_and_list_messages(chat_mgr, project_id):
    chat = await chat_mgr.create_chat(project_id, ChatCreate(title="msg"))
    await chat_mgr.add_message(chat.id, MessageCreate(content="hi", role="user"))
    await chat_mgr.add_message(chat.id, MessageCreate(content="hello", role="assistant"))
    msgs = await chat_mgr.list_messages(chat.id)
    assert len(msgs) == 2
    assert msgs[0].content == "hi"


@pytest.mark.asyncio
async def test_delete_message(chat_mgr, project_id):
    chat = await chat_mgr.create_chat(project_id, ChatCreate(title="dm"))
    msg = await chat_mgr.add_message(chat.id, MessageCreate(content="bye", role="user"))
    await chat_mgr.delete_message(msg.id)
    msgs = await chat_mgr.list_messages(chat.id)
    assert len(msgs) == 0


@pytest.mark.asyncio
async def test_fork_chat(chat_mgr, project_id):
    chat = await chat_mgr.create_chat(project_id, ChatCreate(title="fork-src"))
    await chat_mgr.add_message(chat.id, MessageCreate(content="m1", role="user"))
    await chat_mgr.add_message(chat.id, MessageCreate(content="m2", role="assistant"))
    forked = await chat_mgr.fork_chat(chat.id, label="fork-1")
    assert forked.fork_from_chat_id == chat.id
    forked_msgs = await chat_mgr.list_messages(forked.id)
    assert len(forked_msgs) == 2


@pytest.mark.asyncio
async def test_snapshot_create_restore(chat_mgr, project_id):
    chat = await chat_mgr.create_chat(project_id, ChatCreate(title="snap"))
    await chat_mgr.add_message(chat.id, MessageCreate(content="before", role="user"))
    snap = await chat_mgr.create_snapshot(chat.id, label="s1")
    assert snap.chat_id == chat.id
    restored = await chat_mgr.restore_snapshot(snap.id)
    assert restored.id == chat.id


@pytest.mark.asyncio
async def test_snapshot_list_delete(chat_mgr, project_id):
    chat = await chat_mgr.create_chat(project_id, ChatCreate(title="snap2"))
    await chat_mgr.create_snapshot(chat.id, label="a")
    await chat_mgr.create_snapshot(chat.id, label="b")
    snaps = await chat_mgr.list_snapshots(chat.id)
    assert len(snaps) == 2
    await chat_mgr.delete_snapshot(snaps[0].id)
    snaps2 = await chat_mgr.list_snapshots(chat.id)
    assert len(snaps2) == 1


@pytest.mark.asyncio
async def test_chat_not_found(chat_mgr):
    with pytest.raises(ChatNotFound):
        await chat_mgr.get_chat("nonexistent")
