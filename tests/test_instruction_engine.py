import pytest
from pydantic import ValidationError

from project_service import config
from project_service.engine.instruction_engine import InstructionEngine
from project_service.engine.project_manager import ProjectManager, ProjectNotFound
from project_service.models.instruction import InstructionSave
from project_service.models.project import ProjectCreate


async def test_get_empty(instruction_engine: InstructionEngine, project_manager: ProjectManager):
    p = await project_manager.create(ProjectCreate(name="A"))
    ic = await instruction_engine.get(p.id)
    assert ic.content == ""


async def test_save_and_get(instruction_engine: InstructionEngine, project_manager: ProjectManager):
    p = await project_manager.create(ProjectCreate(name="A"))
    ic = await instruction_engine.save(p.id, InstructionSave(content="hi"))
    assert ic.content == "hi"
    got = await instruction_engine.get(p.id)
    assert got.content == "hi"


async def test_save_snapshots_previous(instruction_engine: InstructionEngine, project_manager: ProjectManager):
    p = await project_manager.create(ProjectCreate(name="A"))
    await instruction_engine.save(p.id, InstructionSave(content="v1"))
    await instruction_engine.save(p.id, InstructionSave(content="v2"))
    snaps = await instruction_engine.list_snapshots(p.id)
    assert len(snaps) == 1
    assert snaps[0].content == "v1"


async def test_clear(instruction_engine: InstructionEngine, project_manager: ProjectManager):
    p = await project_manager.create(ProjectCreate(name="A"))
    await instruction_engine.save(p.id, InstructionSave(content="hi"))
    assert await instruction_engine.clear(p.id) is True
    assert (await instruction_engine.get(p.id)).content == ""


async def test_save_nonexistent_project(instruction_engine: InstructionEngine):
    with pytest.raises(ProjectNotFound):
        await instruction_engine.save("nope", InstructionSave(content="x"))


def test_instruction_save_max_length():
    with pytest.raises(ValidationError):
        InstructionSave(content="x" * (config.MAX_INSTRUCTION_CHARS + 1))
