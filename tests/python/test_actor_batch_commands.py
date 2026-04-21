import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

from handlers import actor_batch_commands  # noqa: E402


class FakeMeshComponent:
    def __init__(self):
        self.mesh = None
        self.materials = {}

    def set_static_mesh(self, mesh):
        self.mesh = mesh

    def set_material(self, slot, material):
        self.materials[slot] = material


class FakeActor:
    def __init__(self, label, component=None):
        self._label = label
        self._component = component or FakeMeshComponent()
        self.folder = None
        self.props = {}

    def get_actor_label(self):
        return self._label

    def get_name(self):
        return self._label

    def get_components_by_class(self, _cls):
        return [self._component]

    def set_folder_path(self, path):
        self.folder = path

    def set_editor_property(self, key, value):
        self.props[key] = value


class FakeActorSubsystem:
    def __init__(self, actors=None, duplicates=None):
        self.actors = list(actors or [])
        self._duplicates = duplicates or []
        self.selected = None

    def get_all_level_actors(self):
        return self.actors

    def duplicate_actors(self, actors, offset):
        return self._duplicates

    def set_selected_level_actors(self, actors):
        self.selected = list(actors)


class FakeAssetLib:
    def __init__(self, existing=None, loaded=None):
        self.existing = set(existing or [])
        self.loaded = loaded or {}

    def does_asset_exist(self, path):
        return path in self.existing

    def load_asset(self, path):
        return self.loaded.get(path)


def _make_unreal(actors=None, duplicates=None, existing=None, loaded=None, static_mesh_cls=None):
    mod = types.SimpleNamespace()
    sub = FakeActorSubsystem(actors=actors or [], duplicates=duplicates or [])
    mod.EditorActorSubsystem = object
    mod.get_editor_subsystem = lambda cls: sub if cls is mod.EditorActorSubsystem else None
    mod.Vector = lambda x, y, z: (x, y, z)
    mod.StaticMeshComponent = object
    mod.StaticMesh = static_mesh_cls
    mod.EditorAssetLibrary = FakeAssetLib(existing=existing, loaded=loaded)
    mod._subsystem = sub
    return mod


# duplicate_actors -----------------------------------------------------------


def test_duplicate_actors_requires_names():
    resp = actor_batch_commands.handle_duplicate_actors({})
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_duplicate_actors_missing(monkeypatch):
    mod = _make_unreal(actors=[FakeActor("A")])
    monkeypatch.setattr(actor_batch_commands, "_get_unreal_module", lambda: mod)
    resp = actor_batch_commands.handle_duplicate_actors({"actor_names": ["A", "B"]})
    assert resp["error_code"] == "ACTOR_NOT_FOUND"
    assert "B" in resp["data"]["missing_actors"]


def test_duplicate_actors_success(monkeypatch):
    src = [FakeActor("A")]
    dupes = [FakeActor("A_Copy")]
    mod = _make_unreal(actors=src, duplicates=dupes)
    monkeypatch.setattr(actor_batch_commands, "_get_unreal_module", lambda: mod)
    resp = actor_batch_commands.handle_duplicate_actors(
        {"actor_names": ["A"], "offset": [100, 0, 0]}
    )
    assert resp["success"] is True
    assert resp["data"]["source_count"] == 1
    assert resp["data"]["duplicates"][0]["label"] == "A_Copy"


# replace_static_mesh --------------------------------------------------------


def test_replace_static_mesh_missing_mesh(monkeypatch):
    mod = _make_unreal(actors=[FakeActor("A")], existing=set())
    monkeypatch.setattr(actor_batch_commands, "_get_unreal_module", lambda: mod)
    resp = actor_batch_commands.handle_replace_static_mesh(
        {"actor_names": ["A"], "mesh_path": "/Game/Missing"}
    )
    assert resp["error_code"] == "MESH_NOT_FOUND"


def test_replace_static_mesh_success(monkeypatch):
    class SM: pass
    mesh = SM()
    component = FakeMeshComponent()
    actor = FakeActor("A", component=component)
    mod = _make_unreal(
        actors=[actor],
        existing={"/Game/Mesh"},
        loaded={"/Game/Mesh": mesh},
        static_mesh_cls=SM,
    )
    monkeypatch.setattr(actor_batch_commands, "_get_unreal_module", lambda: mod)
    resp = actor_batch_commands.handle_replace_static_mesh(
        {"actor_names": ["A"], "mesh_path": "/Game/Mesh"}
    )
    assert resp["success"] is True
    assert component.mesh is mesh
    assert resp["data"]["updated_actors"] == ["A"]


# replace_material -----------------------------------------------------------


def test_replace_material_missing_material(monkeypatch):
    mod = _make_unreal(actors=[FakeActor("A")], existing=set())
    monkeypatch.setattr(actor_batch_commands, "_get_unreal_module", lambda: mod)
    resp = actor_batch_commands.handle_replace_material(
        {"actor_names": ["A"], "material_path": "/Game/M"}
    )
    assert resp["error_code"] == "MATERIAL_NOT_FOUND"


def test_replace_material_success(monkeypatch):
    material = object()
    component = FakeMeshComponent()
    actor = FakeActor("A", component=component)
    mod = _make_unreal(
        actors=[actor], existing={"/Game/M"}, loaded={"/Game/M": material}
    )
    monkeypatch.setattr(actor_batch_commands, "_get_unreal_module", lambda: mod)
    resp = actor_batch_commands.handle_replace_material(
        {"actor_names": ["A"], "material_path": "/Game/M", "slot_index": 1}
    )
    assert resp["success"] is True
    assert component.materials[1] is material


# group_actors ---------------------------------------------------------------


def test_group_actors_requires_params():
    resp = actor_batch_commands.handle_group_actors({"actor_names": ["A"]})
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_group_actors_success(monkeypatch):
    actor = FakeActor("A")
    mod = _make_unreal(actors=[actor])
    monkeypatch.setattr(actor_batch_commands, "_get_unreal_module", lambda: mod)
    resp = actor_batch_commands.handle_group_actors(
        {"actor_names": ["A"], "group_name": "Props/Walls"}
    )
    assert resp["success"] is True
    assert actor.folder == "/Props/Walls"
    assert resp["data"]["grouped_actors"] == ["A"]


# select_actors --------------------------------------------------------------


def test_select_actors_requires_query():
    resp = actor_batch_commands.handle_select_actors({})
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_select_actors_contains_match(monkeypatch):
    actors = [FakeActor("Wall_01"), FakeActor("Wall_02"), FakeActor("Floor_01")]
    mod = _make_unreal(actors=actors)
    monkeypatch.setattr(actor_batch_commands, "_get_unreal_module", lambda: mod)
    resp = actor_batch_commands.handle_select_actors({"query": "Wall"})
    assert resp["success"] is True
    assert set(resp["data"]["selected_actors"]) == {"Wall_01", "Wall_02"}
    assert mod._subsystem.selected is not None
    assert len(mod._subsystem.selected) == 2


def test_select_actors_exact_match(monkeypatch):
    actors = [FakeActor("Wall_01"), FakeActor("Wall_02")]
    mod = _make_unreal(actors=actors)
    monkeypatch.setattr(actor_batch_commands, "_get_unreal_module", lambda: mod)
    resp = actor_batch_commands.handle_select_actors({"query": "Wall_01", "match": "exact"})
    assert resp["data"]["selected_actors"] == ["Wall_01"]
