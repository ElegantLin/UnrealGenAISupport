import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

from handlers import landscape_commands  # noqa: E402


class FakeActor:
    def __init__(self, label):
        self._label = label
        self.props = {}
        self.scale = None

    def get_actor_label(self):
        return self._label

    def get_name(self):
        return self._label

    def set_actor_label(self, label):
        self._label = label

    def set_actor_scale3d(self, scl):
        self.scale = scl

    def set_editor_property(self, key, value):
        self.props[key] = value


class FakeActorSubsystem:
    def __init__(self, actors=None, spawned=None):
        self.actors = list(actors or [])
        self.spawned = spawned

    def get_all_level_actors(self):
        return self.actors

    def spawn_actor_from_class(self, cls, loc, rot):
        return self.spawned


class FakeAssetLib:
    def __init__(self, loaded=None):
        self.loaded = loaded or {}

    def load_asset(self, path):
        return self.loaded.get(path)


def _make_unreal(landscape_cls=object, actors=None, spawned=None, loaded=None):
    import types
    mod = types.SimpleNamespace()
    mod.Landscape = landscape_cls
    mod.EditorActorSubsystem = object
    mod.Vector = lambda x, y, z: (x, y, z)
    mod.Rotator = lambda p, y, r: (p, y, r)
    actor_sub = FakeActorSubsystem(actors=actors or [], spawned=spawned)
    mod.get_editor_subsystem = lambda cls: actor_sub if cls is object else None
    mod.EditorAssetLibrary = FakeAssetLib(loaded=loaded or {})
    return mod


def test_create_landscape_unavailable_without_editor(monkeypatch):
    monkeypatch.setattr(landscape_commands, "_get_unreal_module", lambda: None)
    resp = landscape_commands.handle_create_landscape({})
    assert resp["error_code"] == "UNAVAILABLE_OUTSIDE_EDITOR"


def test_create_landscape_errors_when_landscape_class_missing(monkeypatch):
    class FakeUnrealNoLandscape:
        pass
    mod = FakeUnrealNoLandscape()
    mod.Landscape = None  # type: ignore
    monkeypatch.setattr(landscape_commands, "_get_unreal_module", lambda: mod)
    resp = landscape_commands.handle_create_landscape({})
    assert resp["error_code"] == "LANDSCAPE_UNAVAILABLE"


def test_create_landscape_spawns_actor(monkeypatch):
    class LandscapeCls:
        pass

    spawned = FakeActor("MCP_Landscape")
    mod = _make_unreal(landscape_cls=LandscapeCls, spawned=spawned)
    # Must align cls passed to get_editor_subsystem with EditorActorSubsystem
    mod.EditorActorSubsystem = object
    mod.get_editor_subsystem = lambda cls: FakeActorSubsystem(spawned=spawned) if cls is mod.EditorActorSubsystem else None
    monkeypatch.setattr(landscape_commands, "_get_unreal_module", lambda: mod)
    resp = landscape_commands.handle_create_landscape(
        {"location": [0, 0, 0], "rotation": [0, 0, 0], "actor_label": "TerrainA"}
    )
    assert resp["success"] is True
    assert resp["data"]["actor_label"] == "TerrainA"


def test_create_landscape_warns_when_material_missing(monkeypatch):
    class LandscapeCls:
        pass

    spawned = FakeActor("MCP_Landscape")
    mod = _make_unreal(landscape_cls=LandscapeCls, spawned=spawned, loaded={})
    mod.get_editor_subsystem = lambda cls: FakeActorSubsystem(spawned=spawned)
    monkeypatch.setattr(landscape_commands, "_get_unreal_module", lambda: mod)
    resp = landscape_commands.handle_create_landscape({"material_path": "/Game/M_Missing"})
    assert resp["success"] is True
    assert any("Could not load material" in w for w in resp["warnings"])


def test_set_landscape_material_requires_params():
    resp = landscape_commands.handle_set_landscape_material({})
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_set_landscape_material_actor_not_found(monkeypatch):
    class LandscapeCls:
        pass
    mod = _make_unreal(landscape_cls=LandscapeCls, actors=[])
    mod.get_editor_subsystem = lambda cls: FakeActorSubsystem(actors=[])
    monkeypatch.setattr(landscape_commands, "_get_unreal_module", lambda: mod)
    resp = landscape_commands.handle_set_landscape_material(
        {"actor_name": "Terrain", "material_path": "/Game/M"}
    )
    assert resp["error_code"] == "ACTOR_NOT_FOUND"


def test_set_landscape_material_success(monkeypatch):
    class LandscapeCls(FakeActor):
        pass

    target = LandscapeCls("Terrain")
    material = object()
    mod = _make_unreal(landscape_cls=LandscapeCls, actors=[target], loaded={"/Game/M": material})
    mod.get_editor_subsystem = lambda cls: FakeActorSubsystem(actors=[target])
    monkeypatch.setattr(landscape_commands, "_get_unreal_module", lambda: mod)
    resp = landscape_commands.handle_set_landscape_material(
        {"actor_name": "Terrain", "material_path": "/Game/M"}
    )
    assert resp["success"] is True
    assert target.props["landscape_material"] is material
