import pytest

from project_service.engine.project_manager import (
    ProjectManager,
    ProjectNotArchived,
    ProjectNotFound,
)
from project_service.models.project import ProjectCreate, ProjectUpdate


async def test_create_inits_storage(project_manager: ProjectManager, file_store):
    p = await project_manager.create(ProjectCreate(name="A"))
    assert file_store.has_project(p.id) is True


async def test_get_not_found(project_manager: ProjectManager):
    with pytest.raises(ProjectNotFound):
        await project_manager.get("missing")


async def test_update(project_manager: ProjectManager):
    p = await project_manager.create(ProjectCreate(name="A"))
    upd = await project_manager.update(p.id, ProjectUpdate(name="A2"))
    assert upd.name == "A2"


async def test_star_and_archive(project_manager: ProjectManager):
    p = await project_manager.create(ProjectCreate(name="A"))
    starred = await project_manager.star(p.id, True)
    assert starred.is_starred is True
    arch = await project_manager.archive(p.id)
    assert arch.is_archived is True
    active = await project_manager.list()
    assert all(not r.is_archived for r in active)


async def test_delete_requires_archive(project_manager: ProjectManager, file_store):
    p = await project_manager.create(ProjectCreate(name="A"))
    with pytest.raises(ProjectNotArchived):
        await project_manager.delete(p.id)
    assert file_store.has_project(p.id) is True

    await project_manager.archive(p.id)
    await project_manager.delete(p.id)
    assert file_store.has_project(p.id) is False
    with pytest.raises(ProjectNotFound):
        await project_manager.get(p.id)
