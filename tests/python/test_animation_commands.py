import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

from handlers import animation_commands  # noqa: E402


def test_get_blend_space_info_requires_path(monkeypatch):
    monkeypatch.setattr(animation_commands, "_get_anim_utils", lambda: object())
    resp = animation_commands.handle_get_blend_space_info({})
    assert resp["success"] is False
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_get_blend_space_info_unavailable(monkeypatch):
    monkeypatch.setattr(animation_commands, "_get_anim_utils", lambda: None)
    resp = animation_commands.handle_get_blend_space_info({"blend_space_path": "/Game/BS"})
    assert resp["success"] is False
    assert resp["error_code"] == "UNAVAILABLE_OUTSIDE_EDITOR"


def test_get_blend_space_info_parses_payload(monkeypatch):
    class FakeUtils:
        @staticmethod
        def get_blend_space_info(_path):
            return """{"blend_space_path": "/Game/BS", "axes": [{"name": "X", "min_value": 0, "max_value": 1}], "samples": []}"""

    monkeypatch.setattr(animation_commands, "_get_anim_utils", lambda: FakeUtils)
    resp = animation_commands.handle_get_blend_space_info({"blend_space_path": "/Game/BS"})
    assert resp["success"] is True
    assert resp["data"]["axes"][0]["name"] == "X"


def test_set_axis_validates(monkeypatch):
    monkeypatch.setattr(animation_commands, "_get_anim_utils", lambda: object())
    resp = animation_commands.handle_set_blend_space_axis(
        {"blend_space_path": "/Game/BS", "axis_index": 0, "axis": {"name": "X", "min_value": 2, "max_value": 1}}
    )
    assert resp["success"] is False
    assert resp["error_code"] == "INVALID_AXIS"


def test_replace_samples_rejects_oob(monkeypatch):
    class FakeUtils:
        @staticmethod
        def get_blend_space_info(_path):
            return """{"blend_space_path": "/Game/BS", "axes": [{"name": "X", "min_value": 0, "max_value": 1}], "samples": []}"""

        @staticmethod
        def replace_blend_space_samples(*args):
            raise AssertionError("should not be called when validation fails")

    monkeypatch.setattr(animation_commands, "_get_anim_utils", lambda: FakeUtils)
    resp = animation_commands.handle_replace_blend_space_samples(
        {
            "blend_space_path": "/Game/BS",
            "samples": [{"animation_path": "/Game/A", "coordinates": [5.0]}],
        }
    )
    assert resp["success"] is False
    assert resp["error_code"] == "ASSET_VALIDATION_FAILED"


def test_replace_samples_success_with_verification(monkeypatch):
    info_before = """{"blend_space_path": "/Game/BS", "axes": [{"name": "X", "min_value": 0, "max_value": 1}], "samples": []}"""
    info_after = """{"blend_space_path": "/Game/BS", "axes": [{"name": "X", "min_value": 0, "max_value": 1}], "samples": [{"animation_path": "/Game/A", "coordinates": [0.5]}]}"""

    calls = {"n": 0}

    class FakeUtils:
        @staticmethod
        def get_blend_space_info(_path):
            calls["n"] += 1
            return info_before if calls["n"] == 1 else info_after

        @staticmethod
        def replace_blend_space_samples(_path, _payload):
            return """{"success": true}"""

    monkeypatch.setattr(animation_commands, "_get_anim_utils", lambda: FakeUtils)
    resp = animation_commands.handle_replace_blend_space_samples(
        {
            "blend_space_path": "/Game/BS",
            "samples": [{"animation_path": "/Game/A", "coordinates": [0.5]}],
        }
    )
    assert resp["success"] is True
    assert resp["data"]["applied"] is True
    assert resp["data"]["verification"]["reloaded"] is True
    assert resp["data"]["verification"]["sample_count_match"] is True


def test_set_sample_animation_missing_params(monkeypatch):
    monkeypatch.setattr(animation_commands, "_get_anim_utils", lambda: object())
    resp = animation_commands.handle_set_blend_space_sample_animation({})
    assert resp["success"] is False
    assert resp["error_code"] == "MISSING_PARAMETERS"
