import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

from handlers import session_commands  # noqa: E402


def test_capture_returns_unavailable_when_no_unreal(monkeypatch):
    monkeypatch.setattr(session_commands, "_get_session_utils", lambda: None)
    resp = session_commands.handle_capture_editor_session({})
    assert resp["success"] is False
    assert resp["error_code"] == "UNAVAILABLE_OUTSIDE_EDITOR"


def test_capture_success(monkeypatch):
    class FakeUtils:
        @staticmethod
        def capture_session_json():
            return """{"open_asset_paths":["/Game/A"],"primary_asset_path":"/Game/A"}"""

    monkeypatch.setattr(session_commands, "_get_session_utils", lambda: FakeUtils)
    resp = session_commands.handle_capture_editor_session({})
    assert resp["success"] is True
    assert resp["data"]["primary_asset_path"] == "/Game/A"
    assert resp["data"]["open_asset_paths"][0]["asset_path"] == "/Game/A"


def test_restore_rejects_unknown_policy(monkeypatch):
    monkeypatch.setattr(session_commands, "_get_session_utils", lambda: object())
    resp = session_commands.handle_restore_editor_session({"policy": "whatever"})
    assert resp["success"] is False
    assert resp["error_code"] == "INVALID_RESTORE_POLICY"


def test_restore_policy_none_does_not_invoke_cpp(monkeypatch):
    calls = []

    class FakeUtils:
        @staticmethod
        def load_last_session_json():
            return """{"open_asset_paths":["/Game/A"],"primary_asset_path":"/Game/A"}"""

        @staticmethod
        def open_asset_for_restore(*args):
            calls.append(("open_asset_for_restore", args))
            return """{"success": true}"""

        @staticmethod
        def bring_asset_to_front(*args):
            calls.append(("bring_asset_to_front", args))
            return """{"success": true}"""

        @staticmethod
        def focus_graph(*args):
            calls.append(("focus_graph", args))
            return """{"success": true}"""

    monkeypatch.setattr(session_commands, "_get_session_utils", lambda: FakeUtils)
    resp = session_commands.handle_restore_editor_session({"policy": "none"})
    assert resp["success"] is True
    # policy=none strips assets, so no open_asset_for_restore calls
    open_calls = [c for c in calls if c[0] == "open_asset_for_restore"]
    assert open_calls == []


def test_restore_assets_only_reports_failed(monkeypatch):
    class FakeUtils:
        @staticmethod
        def load_last_session_json():
            return ""

        @staticmethod
        def open_asset_for_restore(path, primary):
            if path == "/Game/Bad":
                return """{"success": false, "error": "missing"}"""
            return """{"success": true}"""

        @staticmethod
        def bring_asset_to_front(path):
            return """{"success": true}"""

        @staticmethod
        def focus_graph(asset, graph):
            return """{"success": true}"""

    monkeypatch.setattr(session_commands, "_get_session_utils", lambda: FakeUtils)
    resp = session_commands.handle_restore_editor_session(
        {
            "policy": "assets_only",
            "snapshot": {
                "open_asset_paths": [
                    {"asset_path": "/Game/Good", "asset_class": "Blueprint"},
                    {"asset_path": "/Game/Bad", "asset_class": "Blueprint"},
                    {"asset_path": "/Game/Mesh", "asset_class": "StaticMesh"},
                ],
                "primary_asset_path": "/Game/Good",
            },
        }
    )
    assert resp["success"] is True
    data = resp["data"]
    assert [r["asset_path"] for r in data["restored_assets"]] == ["/Game/Good"]
    assert [r["asset_path"] for r in data["failed_assets"]] == ["/Game/Bad"]
    assert [r["asset_path"] for r in data["skipped_assets"]] == ["/Game/Mesh"]


def test_focus_graph_requires_params(monkeypatch):
    monkeypatch.setattr(session_commands, "_get_session_utils", lambda: object())
    resp = session_commands.handle_focus_graph({"asset_path": "/Game/A"})
    assert resp["success"] is False
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_save_editor_session_success(monkeypatch):
    class FakeUtils:
        @staticmethod
        def capture_session_json():
            return """{"primary_asset_path":"/Game/A","open_asset_paths":[]}"""

        @staticmethod
        def save_session_json(_payload):
            return """{"success": true}"""

    monkeypatch.setattr(session_commands, "_get_session_utils", lambda: FakeUtils)
    resp = session_commands.handle_save_editor_session({})
    assert resp["success"] is True
    assert resp["data"]["saved"] is True
