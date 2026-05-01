import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

from handlers import level_commands  # noqa: E402


# ---------------------------------------------------------------------------
# Fake unreal shims
# ---------------------------------------------------------------------------


class FakeActor:
    def __init__(self, label="", name=""):
        self._label = label
        self._name = name or label
        self._world_asset = None
        self.props = {}

    def get_actor_label(self):
        return self._label

    def get_name(self):
        return self._name

    def set_actor_label(self, label):
        self._label = label

    def set_editor_property(self, key, value):
        self.props[key] = value

    def set_world_asset(self, asset):
        self._world_asset = asset

    def get_world_asset(self):
        return self._world_asset


class FakeActorEditorPropertyOnly:
    def __init__(self, label=""):
        self._label = label
        self.props = {}

    def get_actor_label(self):
        return self._label

    def set_actor_label(self, label):
        self._label = label

    def set_editor_property(self, key, value):
        self.props[key] = value


class FakeActorWithoutWorldAssetSetter:
    def __init__(self, label=""):
        self._label = label

    def get_actor_label(self):
        return self._label

    def set_actor_label(self, label):
        self._label = label


class FakeWorldAsset:
    def __init__(self, path):
        self._path = path

    def get_path_name(self):
        return self._path


class FakeAssetLibrary:
    def __init__(self, existing=None, loaded=None):
        self.existing = set(existing or [])
        self.loaded = loaded or {}

    def does_asset_exist(self, path):
        return path in self.existing

    def load_asset(self, path):
        return self.loaded.get(path)


class FakeActorSubsystem:
    def __init__(self, actors=None, selected=None, spawned=None):
        self.actors = list(actors or [])
        self.selected = list(selected or [])
        self._spawned = spawned

    def get_all_level_actors(self):
        return self.actors

    def get_selected_level_actors(self):
        return self.selected

    def spawn_actor_from_class(self, cls, location, rotation):
        spawned = self._spawned or FakeActor(label="NewLevelInstance")
        return spawned


class FakeLevelInstanceSubsystem:
    def __init__(self, created_actor=None, raise_error=False):
        self.created_actor = created_actor
        self.raise_error = raise_error
        self.calls = []

    def create_level_instance_from(self, actors, path, pivot_mode, external):
        self.calls.append((list(actors), path, pivot_mode, external))
        if self.raise_error:
            raise RuntimeError("engine failure")
        return self.created_actor


class FakeUnreal:
    class LevelInstance:
        pass

    class EditorActorSubsystem:
        pass

    class LevelInstanceSubsystem:
        pass

    class UnrealEditorSubsystem:
        pass

    class Vector:
        def __init__(self, x, y, z):
            self.x, self.y, self.z = x, y, z

    class Rotator:
        def __init__(self, p, y, r):
            self.p, self.y, self.r = p, y, r

    class Transform:
        def __init__(self, rot, loc, scl):
            self.rotation, self.location, self.scale = rot, loc, scl

    class LevelEditorSubsystem:
        pass

    class GameplayStatics:
        @staticmethod
        def get_all_actors_of_class(world, cls):
            return []

    class EditorLevelUtils:
        calls = []

        @staticmethod
        def add_level_to_world(world, path, streaming_cls):
            FakeUnreal.EditorLevelUtils.calls.append((path, streaming_cls))
            return object()

    class LevelStreamingAlwaysLoaded:
        __name__ = "LevelStreamingAlwaysLoaded"

    class LevelStreamingDynamic:
        __name__ = "LevelStreamingDynamic"

    Actor = object
    EditorAssetLibrary = None  # filled per-test


def _make_unreal(
    *,
    assets=None,
    loaded=None,
    actors=None,
    selected=None,
    level_instance_subsystem=None,
    spawn_returns=None,
    level_editor_new=None,
    level_editor_subsystem=None,
):
    unreal_mod = FakeUnreal()
    unreal_mod.EditorAssetLibrary = FakeAssetLibrary(existing=assets or [], loaded=loaded or {})
    actor_sub = FakeActorSubsystem(actors=actors or [], selected=selected or [], spawned=spawn_returns)

    def get_editor_subsystem(cls):
        if cls is FakeUnreal.EditorActorSubsystem:
            return actor_sub
        if cls is FakeUnreal.LevelInstanceSubsystem:
            return level_instance_subsystem
        if cls is FakeUnreal.LevelEditorSubsystem:
            return level_editor_subsystem
        if cls is FakeUnreal.UnrealEditorSubsystem:
            class _EditorWorld:
                def get_editor_world(self):
                    return object()
            return _EditorWorld()
        return None

    unreal_mod.get_editor_subsystem = get_editor_subsystem
    unreal_mod._actor_subsystem = actor_sub
    return unreal_mod


# ---------------------------------------------------------------------------
# create_level_from_template
# ---------------------------------------------------------------------------


def test_create_level_from_template_requires_path():
    resp = level_commands.handle_create_level_from_template({})
    assert resp["success"] is False
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_create_level_from_template_rejects_unknown_template(monkeypatch):
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: FakeUnreal())
    resp = level_commands.handle_create_level_from_template(
        {"level_path": "/Game/Maps/Foo", "template": "Bogus"}
    )
    assert resp["success"] is False
    assert resp["error_code"] == "LEVEL_TEMPLATE_NOT_FOUND"


def test_create_level_from_template_unavailable(monkeypatch):
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: None)
    resp = level_commands.handle_create_level_from_template({"level_path": "/Game/Maps/Foo"})
    assert resp["error_code"] == "UNAVAILABLE_OUTSIDE_EDITOR"


def test_create_level_from_template_success(monkeypatch):
    class FakeSubsystem:
        def __init__(self):
            self.calls = []

        def new_level_from_template(self, level_path, template):
            self.calls.append((level_path, template))
            return True

    fake_sub = FakeSubsystem()
    unreal_mod = _make_unreal(level_editor_subsystem=fake_sub)
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)

    resp = level_commands.handle_create_level_from_template(
        {"level_path": "/Game/Maps/Foo", "template": "Basic"}
    )
    assert resp["success"] is True
    assert resp["data"]["level_path"] == "/Game/Maps/Foo"
    assert resp["data"]["template"].endswith("Template_Default")
    assert resp["changed_assets"] == ["/Game/Maps/Foo"]


def test_create_level_from_template_failure_is_reported(monkeypatch):
    class FakeSubsystem:
        def new_level_from_template(self, level_path, template):
            return False

    unreal_mod = _make_unreal(level_editor_subsystem=FakeSubsystem())
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)
    resp = level_commands.handle_create_level_from_template(
        {"level_path": "/Game/Maps/Foo", "template": "Basic"}
    )
    assert resp["success"] is False
    assert resp["error_code"] == "LEVEL_OPERATION_FAILED"


# ---------------------------------------------------------------------------
# create_level_instance_from_selection
# ---------------------------------------------------------------------------


def test_create_level_instance_requires_output_path(monkeypatch):
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: FakeUnreal())
    resp = level_commands.handle_create_level_instance_from_selection({})
    assert resp["success"] is False
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_create_level_instance_empty_selection(monkeypatch):
    unreal_mod = _make_unreal(actors=[], selected=[])
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)
    resp = level_commands.handle_create_level_instance_from_selection(
        {"output_level_path": "/Game/Maps/Instance"}
    )
    assert resp["success"] is False
    assert resp["error_code"] == "LEVEL_SELECTION_EMPTY"


def test_create_level_instance_missing_named_actors(monkeypatch):
    unreal_mod = _make_unreal(actors=[FakeActor("Wall_01")])
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)
    resp = level_commands.handle_create_level_instance_from_selection(
        {"output_level_path": "/Game/Maps/Instance", "actor_names": ["Wall_01", "Wall_99"]}
    )
    assert resp["success"] is False
    assert resp["error_code"] == "ACTOR_NOT_FOUND"
    assert "Wall_99" in resp["data"]["missing_actors"]


def test_create_level_instance_success(monkeypatch):
    actors = [FakeActor("Wall_01"), FakeActor("Wall_02")]
    created = FakeActor(label="LI_Instance")
    ls = FakeLevelInstanceSubsystem(created_actor=created)
    unreal_mod = _make_unreal(selected=actors, level_instance_subsystem=ls)
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)

    resp = level_commands.handle_create_level_instance_from_selection(
        {"output_level_path": "/Game/Maps/Instance"}
    )
    assert resp["success"] is True
    assert ls.calls and ls.calls[0][1] == "/Game/Maps/Instance"
    assert resp["data"]["level_instance_actor"] == "LI_Instance"


def test_create_level_instance_missing_subsystem(monkeypatch):
    unreal_mod = _make_unreal(selected=[FakeActor("Wall_01")], level_instance_subsystem=None)
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)
    resp = level_commands.handle_create_level_instance_from_selection(
        {"output_level_path": "/Game/Maps/Instance"}
    )
    assert resp["success"] is False
    assert resp["error_code"] == "LEVEL_OPERATION_FAILED"


# ---------------------------------------------------------------------------
# spawn_level_instance
# ---------------------------------------------------------------------------


def test_spawn_level_instance_requires_path():
    resp = level_commands.handle_spawn_level_instance({})
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_spawn_level_instance_missing_asset(monkeypatch):
    unreal_mod = _make_unreal(assets=[])
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)
    resp = level_commands.handle_spawn_level_instance({"level_asset_path": "/Game/Maps/Prefab"})
    assert resp["success"] is False
    assert resp["error_code"] == "ASSET_NOT_FOUND"


def test_spawn_level_instance_success(monkeypatch):
    asset = FakeWorldAsset("/Game/Maps/Prefab")
    spawned = FakeActor(label="LI_Prefab")
    unreal_mod = _make_unreal(
        assets=["/Game/Maps/Prefab"],
        loaded={"/Game/Maps/Prefab": asset},
        spawn_returns=spawned,
    )
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)
    resp = level_commands.handle_spawn_level_instance(
        {"level_asset_path": "/Game/Maps/Prefab", "actor_label": "HousePrefab"}
    )
    assert resp["success"] is True
    assert spawned._world_asset is asset
    assert "world_asset" not in spawned.props
    assert resp["data"]["level_asset_path"] == "/Game/Maps/Prefab"


def test_spawn_level_instance_uses_editor_property_fallback(monkeypatch):
    asset = FakeWorldAsset("/Game/Maps/Prefab")
    spawned = FakeActorEditorPropertyOnly(label="LI_Prefab")
    unreal_mod = _make_unreal(
        assets=["/Game/Maps/Prefab"],
        loaded={"/Game/Maps/Prefab": asset},
        spawn_returns=spawned,
    )
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)

    resp = level_commands.handle_spawn_level_instance(
        {"level_asset_path": "/Game/Maps/Prefab"}
    )

    assert resp["success"] is True
    assert spawned.props["world_asset"] is asset
    assert resp["data"]["level_asset_path"] == "/Game/Maps/Prefab"


def test_spawn_level_instance_reports_missing_world_asset_setter(monkeypatch):
    asset = FakeWorldAsset("/Game/Maps/Prefab")
    spawned = FakeActorWithoutWorldAssetSetter(label="LI_Prefab")
    unreal_mod = _make_unreal(
        assets=["/Game/Maps/Prefab"],
        loaded={"/Game/Maps/Prefab": asset},
        spawn_returns=spawned,
    )
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)

    resp = level_commands.handle_spawn_level_instance(
        {"level_asset_path": "/Game/Maps/Prefab"}
    )

    assert resp["success"] is False
    assert resp["error_code"] == "LEVEL_OPERATION_FAILED"
    assert "world-asset setter" in resp["message"]


# ---------------------------------------------------------------------------
# add_level_to_world
# ---------------------------------------------------------------------------


def test_add_level_to_world_refuses_forbidden_streaming_class(monkeypatch):
    unreal_mod = _make_unreal(assets=["/Game/Maps/Prefab"])
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)
    resp = level_commands.handle_add_level_to_world(
        {
            "level_path": "/Game/Maps/Prefab",
            "mode": "sublevel",
            "streaming_class": "LevelStreamingLevelInstanceEditor",
        }
    )
    assert resp["success"] is False
    assert resp["error_code"] == "LEVEL_INSTANCE_UNSAFE"
    assert any("spawn_level_instance" in w for w in resp.get("warnings", []))


def test_add_level_to_world_rejects_unknown_mode(monkeypatch):
    unreal_mod = _make_unreal(assets=["/Game/Maps/Prefab"])
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)
    resp = level_commands.handle_add_level_to_world(
        {"level_path": "/Game/Maps/Prefab", "mode": "instanced"}
    )
    assert resp["error_code"] == "LEVEL_MODE_UNSUPPORTED"


def test_add_level_to_world_sublevel_calls_editor_level_utils(monkeypatch):
    unreal_mod = _make_unreal(assets=["/Game/Maps/Prefab"])
    FakeUnreal.EditorLevelUtils.calls = []
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)
    resp = level_commands.handle_add_level_to_world(
        {"level_path": "/Game/Maps/Prefab", "mode": "sublevel"}
    )
    assert resp["success"] is True
    assert FakeUnreal.EditorLevelUtils.calls
    assert resp["data"]["mode"] == "sublevel"


def test_add_level_to_world_level_instance_delegates_to_spawn(monkeypatch):
    asset = FakeWorldAsset("/Game/Maps/Prefab")
    spawned = FakeActor(label="LI_Prefab")
    unreal_mod = _make_unreal(
        assets=["/Game/Maps/Prefab"],
        loaded={"/Game/Maps/Prefab": asset},
        spawn_returns=spawned,
    )
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)
    resp = level_commands.handle_add_level_to_world(
        {"level_path": "/Game/Maps/Prefab", "mode": "level_instance"}
    )
    assert resp["success"] is True
    assert resp["data"]["mode"] == "level_instance"


def test_add_level_to_world_missing_asset(monkeypatch):
    unreal_mod = _make_unreal(assets=[])
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)
    resp = level_commands.handle_add_level_to_world(
        {"level_path": "/Game/Maps/Missing", "mode": "sublevel"}
    )
    assert resp["error_code"] == "ASSET_NOT_FOUND"


# ---------------------------------------------------------------------------
# list_level_instances
# ---------------------------------------------------------------------------


def test_list_level_instances_returns_empty_when_world_has_none(monkeypatch):
    unreal_mod = _make_unreal(actors=[])
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)
    resp = level_commands.handle_list_level_instances({})
    assert resp["success"] is True
    assert resp["data"]["count"] == 0


def test_list_level_instances_lists_candidates(monkeypatch):
    class FakeLevelInstanceActor(FakeActor):
        pass

    # Swap in a new subclass as the unreal.LevelInstance class so isinstance works.
    actors = [FakeLevelInstanceActor(label="House"), FakeActor(label="OtherActor")]
    unreal_mod = _make_unreal(actors=actors)
    unreal_mod.LevelInstance = FakeLevelInstanceActor  # type: ignore
    actors[0]._world_asset = FakeWorldAsset("/Game/Maps/House")
    monkeypatch.setattr(level_commands, "_get_unreal_module", lambda: unreal_mod)

    resp = level_commands.handle_list_level_instances({})
    assert resp["success"] is True
    assert resp["data"]["count"] == 1
    assert resp["data"]["level_instances"][0]["label"] == "House"
    assert resp["data"]["level_instances"][0]["world_asset"] == "/Game/Maps/House"
