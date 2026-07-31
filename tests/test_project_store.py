from project_service.store.project_store import ProjectStore


def test_create_and_get(store: ProjectStore):
    row = store.create_project({"name": "A", "description": "d"})
    assert row["name"] == "A"
    got = store.get_project(row["id"])
    assert got["description"] == "d"
    assert got["is_archived"] == 0
    assert got["is_starred"] == 0


def test_get_missing(store: ProjectStore):
    assert store.get_project("nope") is None


def test_list_filters(store: ProjectStore):
    a = store.create_project({"name": "A"})
    store.set_starred(a["id"], True)
    b = store.create_project({"name": "B"})
    store.set_archived(b["id"], True)

    active = store.list_projects()
    assert {r["id"] for r in active} == {a["id"]}

    starred = store.list_projects(only_starred=True)
    assert {r["id"] for r in starred} == {a["id"]}

    all_rows = store.list_projects(include_archived=True)
    assert len(all_rows) == 2


def test_update(store: ProjectStore):
    p = store.create_project({"name": "A"})
    row = store.update_project(p["id"], {"name": "A2", "description": "x"})
    assert row["name"] == "A2"
    assert row["description"] == "x"


def test_update_unknown_field_ignored(store: ProjectStore):
    p = store.create_project({"name": "A"})
    row = store.update_project(p["id"], {"name": "A2", "evil": "hax"})
    assert row["name"] == "A2"


def test_delete_cascades(store: ProjectStore):
    p = store.create_project({"name": "A"})
    store.save_instructions(p["id"], "hello")
    assert store.delete_project(p["id"]) is True
    assert store.get_project(p["id"]) is None
    assert store.get_instructions(p["id"]) is None


def test_instructions_save_get_clear(store: ProjectStore):
    p = store.create_project({"name": "A"})
    assert store.get_instructions(p["id"]) is None

    row = store.save_instructions(p["id"], "v1")
    assert row["content"] == "v1"
    assert store.get_instructions(p["id"])["content"] == "v1"

    row2 = store.save_instructions(p["id"], "v2")
    assert row2["content"] == "v2"

    assert store.clear_instructions(p["id"]) is True
    assert store.get_instructions(p["id"]) is None
    assert store.clear_instructions(p["id"]) is False


def test_snapshots(store: ProjectStore):
    p = store.create_project({"name": "A"})
    snap = store.snapshot_instruction(p["id"], "c", label="L")
    assert snap["label"] == "L"
    rows = store.list_snapshots(p["id"])
    assert len(rows) == 1
