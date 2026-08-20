import pytest

from project_service.engine.chat_manager import ChatManager
from project_service.engine.knowledge_manager import KnowledgeManager
from project_service.engine.project_manager import ProjectManager
from project_service.models.chat import ChatCreate, MessageCreate
from project_service.models.knowledge import FolderCreate
from project_service.models.project import ProjectCreate
from project_service.store.file_store import FileStore
from project_service.store.project_store import ProjectStore


@pytest.fixture
def pm(tmp_path):
    store = ProjectStore(db_path=tmp_path / "projects.db")
    mgr = ProjectManager(store=store, file_store=FileStore(storage_dir=tmp_path / "storage"))
    mgr.rag_coordinator = None
    yield mgr
    store.close()


@pytest.fixture
def km(pm):
    return KnowledgeManager(store=pm.store, project_manager=pm)


@pytest.fixture
def cm(pm):
    return ChatManager(store=pm.store, project_manager=pm)


@pytest.mark.asyncio
async def test_template_id_copies_instructions(pm):
    template = await pm.create(ProjectCreate(name="tmpl", instructions="always answer in Chinese"))
    new_proj = await pm.create(ProjectCreate(name="from-tmpl", template_id=template.id))
    instr = pm.store.get_instructions(new_proj.id)
    assert instr is not None
    assert instr["content"] == "always answer in Chinese"
    assert new_proj.id != template.id


@pytest.mark.asyncio
async def test_template_id_copies_knowledge_folders_and_files(pm, km, tmp_path):
    template = await pm.create(ProjectCreate(name="tmpl-kb"))
    folder = await km.create_folder(template.id, FolderCreate(name="docs"))
    src = tmp_path / "note.txt"
    src.write_text("hello kb")
    await km.upload_file(template.id, str(src), "note.txt", folder_id=folder.id)

    new_proj = await pm.create(ProjectCreate(name="from-tmpl-kb", template_id=template.id))

    folders = await km.list_folders(new_proj.id)
    assert len(folders) == 1
    assert folders[0].name == "docs"
    files = await km.list_files(new_proj.id, folder_id=folders[0].id)
    assert len(files) == 1
    assert files[0].original_name == "note.txt"
    assert files[0].index_status == "PENDING"
    from pathlib import Path
    assert Path(files[0].file_path).exists()


@pytest.mark.asyncio
async def test_duplicate_copies_knowledge_files_disk(pm, km, tmp_path):
    source = await pm.create(ProjectCreate(name="src-dup"))
    src = tmp_path / "data.txt"
    src.write_text("payload")
    await km.upload_file(source.id, str(src), "data.txt")

    dup = await pm.duplicate_project(source.id, name="dup-dup")
    files = await km.list_files(dup.id)
    assert len(files) == 1
    assert files[0].original_name == "data.txt"
    from pathlib import Path
    copied = Path(files[0].file_path)
    assert copied.exists()
    assert copied.read_text() == "payload"
    assert dup.id != source.id


@pytest.mark.asyncio
async def test_duplicate_copy_chats_true(pm, km, cm):
    source = await pm.create(ProjectCreate(name="src-chat"))
    chat = await cm.create_chat(source.id, ChatCreate(title="talk"))
    await cm.add_message(chat.id, MessageCreate(content="hi", role="user"))
    await cm.add_message(chat.id, MessageCreate(content="hello", role="assistant"))

    dup = await pm.duplicate_project(source.id, name="dup-chat", copy_chats=True)
    chats = await cm.list_chats(dup.id)
    assert len(chats) == 1
    assert chats[0].title == "talk"
    msgs = await cm.list_messages(chats[0].id)
    assert len(msgs) == 2
    assert msgs[0].content == "hi"
    assert msgs[1].content == "hello"


@pytest.mark.asyncio
async def test_duplicate_copy_chats_false_default(pm, km, cm):
    source = await pm.create(ProjectCreate(name="src-nochat"))
    await cm.create_chat(source.id, ChatCreate(title="should-not-copy"))

    dup = await pm.duplicate_project(source.id, name="dup-nochat")
    chats = await cm.list_chats(dup.id)
    assert len(chats) == 0


@pytest.mark.asyncio
async def test_duplicate_file_left_pending_without_rag(pm, km, tmp_path):
    source = await pm.create(ProjectCreate(name="src-pending"))
    src = tmp_path / "f.txt"
    src.write_text("x")
    await km.upload_file(source.id, str(src), "f.txt")

    dup = await pm.duplicate_project(source.id, name="dup-pending")
    files = await km.list_files(dup.id)
    assert len(files) == 1
    assert files[0].index_status == "PENDING"
    assert files[0].rag_doc_id is None
