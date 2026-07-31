import pytest

from project_service.engine.knowledge_manager import (
    FolderNotFound,
    KnowledgeFileNotFound,
    KnowledgeManager,
)
from project_service.engine.project_manager import ProjectManager
from project_service.models.knowledge import FolderCreate, FolderUpdate
from project_service.models.project import ProjectCreate
from project_service.store.project_store import ProjectStore


@pytest.fixture
def km(tmp_path):
    store = ProjectStore(db_path=tmp_path / "projects.db")
    pm = ProjectManager(store=store)
    mgr = KnowledgeManager(store=store, project_manager=pm)
    yield mgr
    store.close()


@pytest.fixture
async def project_id(km):
    proj = await km.project_manager.create(ProjectCreate(name="ktest"))
    return proj.id


@pytest.mark.asyncio
async def test_create_and_get_folder(km, project_id):
    folder = await km.create_folder(project_id, FolderCreate(name="docs"))
    assert folder.name == "docs"
    assert folder.project_id == project_id
    got = await km.get_folder(folder.id)
    assert got.id == folder.id


@pytest.mark.asyncio
async def test_list_folders(km, project_id):
    await km.create_folder(project_id, FolderCreate(name="a"))
    await km.create_folder(project_id, FolderCreate(name="b"))
    folders = await km.list_folders(project_id)
    assert len(folders) == 2


@pytest.mark.asyncio
async def test_update_folder(km, project_id):
    folder = await km.create_folder(project_id, FolderCreate(name="old"))
    updated = await km.update_folder(folder.id, FolderUpdate(name="new"))
    assert updated.name == "new"


@pytest.mark.asyncio
async def test_delete_folder(km, project_id):
    folder = await km.create_folder(project_id, FolderCreate(name="del"))
    await km.delete_folder(folder.id)
    with pytest.raises(FolderNotFound):
        await km.get_folder(folder.id)


@pytest.mark.asyncio
async def test_nested_folders(km, project_id):
    parent = await km.create_folder(project_id, FolderCreate(name="parent"))
    child = await km.create_folder(
        project_id, FolderCreate(name="child", parent_id=parent.id)
    )
    assert child.parent_id == parent.id
    children = await km.list_folders(project_id, parent_id=parent.id)
    assert len(children) == 1
    assert children[0].id == child.id


@pytest.mark.asyncio
async def test_create_and_get_file(km, project_id):
    folder = await km.create_folder(project_id, FolderCreate(name="f"))
    kfile = await km.create_file(
        project_id,
        folder_id=folder.id,
        name="readme.md",
        original_name="readme.md",
        file_path="/tmp/readme.md",
        file_size=1024,
        mime_type="text/markdown",
    )
    assert kfile.name == "readme.md"
    assert kfile.index_status == "PENDING"
    got = await km.get_file(kfile.id)
    assert got.id == kfile.id


@pytest.mark.asyncio
async def test_list_files_by_folder(km, project_id):
    f1 = await km.create_folder(project_id, FolderCreate(name="f1"))
    f2 = await km.create_folder(project_id, FolderCreate(name="f2"))
    await km.create_file(
        project_id, folder_id=f1.id, name="a.txt",
        original_name="a.txt", file_path="/a", file_size=10, mime_type="text/plain",
    )
    await km.create_file(
        project_id, folder_id=f2.id, name="b.txt",
        original_name="b.txt", file_path="/b", file_size=20, mime_type="text/plain",
    )
    files_f1 = await km.list_files(project_id, folder_id=f1.id)
    assert len(files_f1) == 1
    assert files_f1[0].name == "a.txt"


@pytest.mark.asyncio
async def test_update_file_status(km, project_id):
    folder = await km.create_folder(project_id, FolderCreate(name="f"))
    kfile = await km.create_file(
        project_id, folder_id=folder.id, name="x.py",
        original_name="x.py", file_path="/x", file_size=5, mime_type="text/x-python",
    )
    updated = await km.update_file_status(kfile.id, "INDEXING")
    assert updated.index_status == "INDEXING"
    updated2 = await km.update_file_status(kfile.id, "INDEXED", rag_doc_id="doc-123")
    assert updated2.index_status == "INDEXED"
    assert updated2.rag_doc_id == "doc-123"


@pytest.mark.asyncio
async def test_delete_file(km, project_id):
    folder = await km.create_folder(project_id, FolderCreate(name="f"))
    kfile = await km.create_file(
        project_id, folder_id=folder.id, name="d.txt",
        original_name="d.txt", file_path="/d", file_size=1, mime_type="text/plain",
    )
    await km.delete_file(kfile.id)
    with pytest.raises(KnowledgeFileNotFound):
        await km.get_file(kfile.id)


@pytest.mark.asyncio
async def test_list_file_statuses(km, project_id):
    folder = await km.create_folder(project_id, FolderCreate(name="f"))
    await km.create_file(
        project_id, folder_id=folder.id, name="a.txt",
        original_name="a.txt", file_path="/a", file_size=1, mime_type="text/plain",
    )
    await km.create_file(
        project_id, folder_id=folder.id, name="b.txt",
        original_name="b.txt", file_path="/b", file_size=2, mime_type="text/plain",
    )
    statuses = await km.list_file_statuses(project_id)
    assert len(statuses) == 2
    assert all(s.index_status == "PENDING" for s in statuses)


@pytest.mark.asyncio
async def test_folder_not_found(km):
    with pytest.raises(FolderNotFound):
        await km.get_folder("nonexistent")


@pytest.mark.asyncio
async def test_file_not_found(km):
    with pytest.raises(KnowledgeFileNotFound):
        await km.get_file("nonexistent")
