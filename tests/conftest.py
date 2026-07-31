import pytest

from project_service.store.file_store import FileStore
from project_service.store.project_store import ProjectStore
from project_service.engine.instruction_engine import InstructionEngine
from project_service.engine.project_manager import ProjectManager


@pytest.fixture
def store(tmp_path):
    s = ProjectStore(db_path=tmp_path / "projects.db")
    yield s
    s.close()


@pytest.fixture
def file_store(tmp_path):
    return FileStore(storage_dir=tmp_path / "storage")


@pytest.fixture
def project_manager(store, file_store):
    return ProjectManager(store=store, file_store=file_store)


@pytest.fixture
def instruction_engine(store, project_manager):
    return InstructionEngine(store=store, project_manager=project_manager)
