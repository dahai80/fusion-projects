import pytest

from project_service.engine.project_manager import (
    ArtifactAlreadyMigrated,
    ArtifactNotFound,
    ProjectManager,
)
from project_service.models.artifact_ref import ArtifactRef
from project_service.store.project_store import ProjectStore


@pytest.fixture
def store(tmp_path):
    s = ProjectStore(db_path=tmp_path / "projects.db")
    yield s
    s.close()


@pytest.fixture
def pm(store):
    return ProjectManager(store=store)


@pytest.mark.asyncio
async def test_create_and_list_artifact_ref(pm, store):
    row = store.create_project({"name": "P1"})
    pid = row["id"]
    ref_data = {
        "project_id": pid,
        "artifact_id": "art_abc123",
        "artifact_name": "dashboard.tsx",
        "artifact_type": "react",
        "artifact_kind": "app",
        "content_summary": "Sales dashboard",
        "source_session_id": "sess_1",
    }
    store.create_artifact_ref(ref_data)
    refs = await pm.list_artifacts(pid)
    assert len(refs) == 1
    assert refs[0].artifact_id == "art_abc123"
    assert refs[0].artifact_name == "dashboard.tsx"
    assert refs[0].artifact_type == "react"
    assert refs[0].artifact_kind == "app"


@pytest.mark.asyncio
async def test_remove_artifact_ref(pm, store):
    row = store.create_project({"name": "P2"})
    pid = row["id"]
    store.create_artifact_ref({
        "project_id": pid,
        "artifact_id": "art_xyz",
        "artifact_name": "foo.py",
        "artifact_type": "code",
    })
    ok = await pm.remove_artifact("art_xyz")
    assert ok is True
    refs = await pm.list_artifacts(pid)
    assert len(refs) == 0


@pytest.mark.asyncio
async def test_remove_nonexistent_artifact_raises(pm):
    with pytest.raises(ArtifactNotFound):
        await pm.remove_artifact("art_nonexistent")


@pytest.mark.asyncio
async def test_duplicate_artifact_ref_raises(store, pm):
    row = store.create_project({"name": "P3"})
    pid = row["id"]
    store.create_artifact_ref({
        "project_id": pid,
        "artifact_id": "art_dup",
        "artifact_name": "dup.py",
        "artifact_type": "code",
    })
    with pytest.raises(ArtifactAlreadyMigrated):
        await pm.migrate_artifact(pid, "art_dup")


def test_artifact_ref_from_row():
    row = {
        "id": "ref_1",
        "project_id": "proj_1",
        "artifact_id": "art_1",
        "artifact_name": "hello.py",
        "artifact_type": "code",
        "artifact_kind": "code",
        "content_summary": "test",
        "migrated_at": "2026-07-31T00:00:00+00:00",
        "source_session_id": "sess_1",
    }
    ref = ArtifactRef.from_row(row)
    assert ref.id == "ref_1"
    assert ref.artifact_name == "hello.py"
    assert ref.artifact_kind == "code"


def test_count_artifact_refs(store):
    row = store.create_project({"name": "P4"})
    pid = row["id"]
    assert store.count_artifact_refs(pid) == 0
    store.create_artifact_ref({
        "project_id": pid,
        "artifact_id": "art_a",
        "artifact_name": "a.py",
        "artifact_type": "code",
    })
    store.create_artifact_ref({
        "project_id": pid,
        "artifact_id": "art_b",
        "artifact_name": "b.py",
        "artifact_type": "code",
    })
    assert store.count_artifact_refs(pid) == 2
