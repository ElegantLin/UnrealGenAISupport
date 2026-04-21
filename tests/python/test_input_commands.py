import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

from handlers import input_commands  # noqa: E402


def test_create_input_action_validates_name(monkeypatch):
    monkeypatch.setattr(input_commands, "_get_input_utils", lambda: object())
    resp = input_commands.handle_create_input_action({})
    assert resp["success"] is False
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_create_input_action_invalid_value_type(monkeypatch):
    monkeypatch.setattr(input_commands, "_get_input_utils", lambda: object())
    resp = input_commands.handle_create_input_action(
        {"name": "IA_Jump", "value_type": "WrongType"}
    )
    assert resp["success"] is False
    assert resp["error_code"] == "INVALID_VALUE_TYPE"


def test_create_input_action_success(monkeypatch):
    class FakeUtils:
        @staticmethod
        def create_input_action(name, save_path, value_type, description):
            return f'{{"success": true, "asset_path": "{save_path}/{name}"}}'

    monkeypatch.setattr(input_commands, "_get_input_utils", lambda: FakeUtils)
    resp = input_commands.handle_create_input_action(
        {"name": "IA_Jump", "value_type": "Digital", "save_path": "/Game/Input"}
    )
    assert resp["success"] is True
    assert resp["data"]["asset_path"] == "/Game/Input/IA_Jump"


def test_map_enhanced_input_requires_context(monkeypatch):
    monkeypatch.setattr(input_commands, "_get_input_utils", lambda: object())
    resp = input_commands.handle_map_enhanced_input_action({"action_path": "/Game/IA"})
    assert resp["success"] is False
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_map_enhanced_input_invalid_trigger(monkeypatch):
    monkeypatch.setattr(input_commands, "_get_input_utils", lambda: object())
    resp = input_commands.handle_map_enhanced_input_action(
        {
            "context_path": "/Game/IMC",
            "action_path": "/Game/IA",
            "key": "E",
            "triggers": ["Whenever"],
        }
    )
    assert resp["success"] is False
    assert resp["error_code"] == "INVALID_BINDING"


def test_map_enhanced_input_success(monkeypatch):
    seen = {}

    class FakeUtils:
        @staticmethod
        def map_enhanced_input_action(context, action, key, triggers_json, modifiers_json):
            seen["context"] = context
            seen["action"] = action
            seen["key"] = key
            seen["triggers"] = triggers_json
            seen["modifiers"] = modifiers_json
            return """{"success": true}"""

    monkeypatch.setattr(input_commands, "_get_input_utils", lambda: FakeUtils)
    resp = input_commands.handle_map_enhanced_input_action(
        {
            "context_path": "/Game/IMC",
            "action_path": "/Game/IA_Jump",
            "key": "space",
            "triggers": ["pressed"],
            "modifiers": ["negate"],
        }
    )
    assert resp["success"] is True
    assert resp["data"]["binding"]["key"] == "SpaceBar"
    assert seen["triggers"] == '["Pressed"]'
    assert seen["modifiers"] == '["Negate"]'


def test_list_input_mappings_shape(monkeypatch):
    class FakeUtils:
        @staticmethod
        def list_input_mappings(context):
            return """{"mappings": [{"action_path": "/Game/IA_Jump", "key": "SpaceBar"}]}"""

    monkeypatch.setattr(input_commands, "_get_input_utils", lambda: FakeUtils)
    resp = input_commands.handle_list_input_mappings({"context_path": "/Game/IMC"})
    assert resp["success"] is True
    assert resp["data"]["mappings"][0]["key"] == "SpaceBar"


def test_legacy_warning_helper():
    resp = input_commands.handle_legacy_input_binding_warning({"project_uses_enhanced_input": True})
    assert resp["success"] is True
    assert resp["data"]["warning"]["error_code"] == "LEGACY_INPUT_PATH"


def test_unavailable_when_no_unreal(monkeypatch):
    monkeypatch.setattr(input_commands, "_get_input_utils", lambda: None)
    resp = input_commands.handle_create_input_mapping_context({"name": "IMC_Default"})
    assert resp["success"] is False
    assert resp["error_code"] == "UNAVAILABLE_OUTSIDE_EDITOR"
