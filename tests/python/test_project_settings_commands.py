import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

from handlers import project_settings_commands  # noqa: E402


class FakeCDO:
    def __init__(self, fail_set=False):
        self.props = {}
        self.saved = False
        self.fail_set = fail_set

    def set_editor_property(self, key, value):
        if self.fail_set:
            raise RuntimeError("set failed")
        self.props[key] = value

    def save_config(self):
        self.saved = True


class FakeClass:
    def __init__(self, cdo):
        self._cdo = cdo

    def get_default_object(self):
        return self._cdo


def _make_unreal(class_map=None):
    import types
    mod = types.SimpleNamespace()
    class_map = class_map or {}

    def load_class(_outer, path):
        return class_map.get(path)

    mod.load_class = load_class
    return mod


def test_set_project_setting_requires_value():
    resp = project_settings_commands.handle_set_project_setting(
        {"settings_class": "/Script/Engine.RendererSettings", "key": "bFoo"}
    )
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_set_project_setting_requires_class():
    resp = project_settings_commands.handle_set_project_setting({"value": True, "key": "bFoo"})
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_set_project_setting_unavailable(monkeypatch):
    monkeypatch.setattr(project_settings_commands, "_get_unreal_module", lambda: None)
    resp = project_settings_commands.handle_set_project_setting(
        {"settings_class": "/Script/Engine.RendererSettings", "key": "bFoo", "value": True}
    )
    assert resp["error_code"] == "UNAVAILABLE_OUTSIDE_EDITOR"


def test_set_project_setting_class_not_found(monkeypatch):
    mod = _make_unreal({})
    monkeypatch.setattr(project_settings_commands, "_get_unreal_module", lambda: mod)
    resp = project_settings_commands.handle_set_project_setting(
        {"settings_class": "/Script/Engine.Missing", "key": "bFoo", "value": True}
    )
    assert resp["error_code"] == "PROJECT_SETTING_NOT_FOUND"


def test_set_project_setting_success(monkeypatch):
    cdo = FakeCDO()
    mod = _make_unreal({"/Script/Engine.RendererSettings": FakeClass(cdo)})
    monkeypatch.setattr(project_settings_commands, "_get_unreal_module", lambda: mod)
    resp = project_settings_commands.handle_set_project_setting(
        {"settings_class": "/Script/Engine.RendererSettings", "key": "bDefaultFeatureBloom", "value": False}
    )
    assert resp["success"] is True
    assert cdo.props["bDefaultFeatureBloom"] is False
    assert cdo.saved is True
    assert resp["data"]["saved_to_config"] is True


def test_set_project_setting_failure_returns_project_setting_failed(monkeypatch):
    cdo = FakeCDO(fail_set=True)
    mod = _make_unreal({"/Script/Engine.RendererSettings": FakeClass(cdo)})
    monkeypatch.setattr(project_settings_commands, "_get_unreal_module", lambda: mod)
    resp = project_settings_commands.handle_set_project_setting(
        {"settings_class": "/Script/Engine.RendererSettings", "key": "bFoo", "value": True}
    )
    assert resp["error_code"] == "PROJECT_SETTING_FAILED"


def test_set_rendering_defaults_applies_known_toggles(monkeypatch):
    cdo = FakeCDO()
    mod = _make_unreal({"/Script/Engine.RendererSettings": FakeClass(cdo)})
    monkeypatch.setattr(project_settings_commands, "_get_unreal_module", lambda: mod)
    resp = project_settings_commands.handle_set_rendering_defaults(
        {"auto_exposure": False, "bloom": True}
    )
    assert resp["success"] is True
    assert cdo.props["bDefaultFeatureAutoExposure"] is False
    assert cdo.props["bDefaultFeatureBloom"] is True
    assert len(resp["data"]["changes"]) == 2


def test_set_rendering_defaults_ignores_none_and_unknown_keys(monkeypatch):
    cdo = FakeCDO()
    mod = _make_unreal({"/Script/Engine.RendererSettings": FakeClass(cdo)})
    monkeypatch.setattr(project_settings_commands, "_get_unreal_module", lambda: mod)
    resp = project_settings_commands.handle_set_rendering_defaults(
        {"unknown_key": True, "motion_blur": None}
    )
    assert resp["success"] is True
    assert resp["data"]["changes"] == []


def test_set_rendering_defaults_reports_partial_failure(monkeypatch):
    # Motion blur uses a class we don't provide -> fails; bloom succeeds.
    cdo = FakeCDO()
    mod = _make_unreal({"/Script/Engine.RendererSettings": FakeClass(cdo)})
    monkeypatch.setattr(project_settings_commands, "_get_unreal_module", lambda: mod)
    # Force failure by removing the class after first call? Simpler: point one
    # mapping at a missing class via monkeypatch of RENDERING_SETTING_MAP.
    monkeypatch.setitem(
        project_settings_commands.RENDERING_SETTING_MAP,
        "motion_blur",
        ("/Script/Engine.DoesNotExist", "bMB"),
    )
    resp = project_settings_commands.handle_set_rendering_defaults(
        {"bloom": True, "motion_blur": True}
    )
    assert resp["success"] is True
    oks = [c for c in resp["data"]["changes"] if c["ok"]]
    fails = [c for c in resp["data"]["changes"] if not c["ok"]]
    assert len(oks) == 1 and len(fails) == 1
    assert resp["warnings"]
