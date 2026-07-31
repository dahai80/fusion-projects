import pytest

from project_service.models.artifact_ref import ArtifactRef
from project_service.store.project_store import ProjectStore


@pytest.fixture
def store(tmp_path):
    s = ProjectStore(db_path=tmp_path / "test.db")
    yield s
    s.close()


def _create_project(store, name="P"):
    return store.create_project({"name": name})


def _create_ref(store, project_id, artifact_id, artifact_name="a", artifact_type="html", artifact_kind=None):
    return store.create_artifact_ref({
        "project_id": project_id,
        "artifact_id": artifact_id,
        "artifact_name": artifact_name,
        "artifact_type": artifact_type,
        "artifact_kind": artifact_kind,
    })


def test_list_artifact_refs_filter_by_type(store):
    p = _create_project(store)
    _create_ref(store, p["id"], "art1", artifact_type="html")
    _create_ref(store, p["id"], "art2", artifact_type="code")
    _create_ref(store, p["id"], "art3", artifact_type="html")
    html = store.list_artifact_refs(p["id"], artifact_type="html")
    assert len(html) == 2
    code = store.list_artifact_refs(p["id"], artifact_type="code")
    assert len(code) == 1


def test_list_artifact_refs_filter_by_kind(store):
    p = _create_project(store)
    _create_ref(store, p["id"], "art1", artifact_kind="app")
    _create_ref(store, p["id"], "art2", artifact_kind="tool")
    apps = store.list_artifact_refs(p["id"], artifact_kind="app")
    assert len(apps) == 1
    assert apps[0]["artifact_kind"] == "app"


def test_list_artifact_refs_filter_by_search(store):
    p = _create_project(store)
    _create_ref(store, p["id"], "art1", artifact_name="hello world")
    _create_ref(store, p["id"], "art2", artifact_name="foo bar")
    result = store.list_artifact_refs(p["id"], search="hello")
    assert len(result) == 1
    assert result[0]["artifact_name"] == "hello world"


def test_list_artifact_refs_combined_filters(store):
    p = _create_project(store)
    _create_ref(store, p["id"], "art1", artifact_name="my app", artifact_type="html", artifact_kind="app")
    _create_ref(store, p["id"], "art2", artifact_name="my tool", artifact_type="code", artifact_kind="tool")
    _create_ref(store, p["id"], "art3", artifact_name="other app", artifact_type="html", artifact_kind="app")
    result = store.list_artifact_refs(p["id"], artifact_type="html", artifact_kind="app")
    assert len(result) == 2
    result2 = store.list_artifact_refs(p["id"], artifact_type="html", search="my")
    assert len(result2) == 1


def test_project_artifact_count(store):
    p = _create_project(store)
    assert store.get_project(p["id"]).get("artifact_count", 0) == 0
    _create_ref(store, p["id"], "art1")
    _create_ref(store, p["id"], "art2")
    row = store.get_project(p["id"])
    assert row["artifact_count"] == 2


def test_list_projects_artifact_count(store):
    p1 = _create_project(store, "P1")
    p2 = _create_project(store, "P2")
    _create_ref(store, p1["id"], "art1")
    _create_ref(store, p1["id"], "art2")
    _create_ref(store, p2["id"], "art3")
    rows = store.list_projects()
    counts = {r["name"]: r["artifact_count"] for r in rows}
    assert counts["P1"] == 2
    assert counts["P2"] == 1
