import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

from utils.input_mapping import (  # noqa: E402
    InputMappingError,
    build_binding,
    diff_bindings,
    legacy_binding_warning,
    normalize_key_name,
    normalize_modifier,
    normalize_trigger,
    normalize_value_type,
)


def test_normalize_key_name_aliases_and_casing():
    assert normalize_key_name("e") == "E"
    assert normalize_key_name("LMB") == "LeftMouseButton"
    assert normalize_key_name("space") == "SpaceBar"
    assert normalize_key_name("5") == "Five"
    assert normalize_key_name("Gamepad_FaceButton_Bottom") == "Gamepad_FaceButton_Bottom"


def test_normalize_key_name_rejects_empty():
    with pytest.raises(InputMappingError):
        normalize_key_name("   ")


def test_normalize_trigger_and_modifier_case_insensitive():
    assert normalize_trigger("pressed") == "Pressed"
    assert normalize_modifier("deadzone") == "DeadZone"


def test_normalize_trigger_rejects_unknown():
    with pytest.raises(InputMappingError):
        normalize_trigger("Whenever")


def test_normalize_value_type_default_digital():
    assert normalize_value_type("") == "Digital"
    assert normalize_value_type("axis2d") == "Axis2D"


def test_build_binding_normalizes_all_fields():
    b = build_binding(
        action_path="/Game/IA_Jump",
        key="space",
        triggers=["pressed"],
        modifiers=["deadzone", "negate"],
    )
    assert b.action_path == "/Game/IA_Jump"
    assert b.key == "SpaceBar"
    assert b.triggers == ("Pressed",)
    assert b.modifiers == ("DeadZone", "Negate")


def test_build_binding_requires_action():
    with pytest.raises(InputMappingError):
        build_binding(action_path="", key="E")


def test_diff_bindings_add_and_remove():
    current = [build_binding("/Game/IA_Jump", "space", triggers=["pressed"])]
    desired = [
        build_binding("/Game/IA_Jump", "space", triggers=["pressed"]),
        build_binding("/Game/IA_Fire", "lmb", triggers=["down"]),
    ]
    diff = diff_bindings(current, desired)
    assert len(diff["added"]) == 1
    assert diff["added"][0].action_path == "/Game/IA_Fire"
    assert diff["removed"] == []
    assert len(diff["unchanged"]) == 1


def test_diff_bindings_detects_removed():
    current = [build_binding("/Game/IA_Fire", "lmb")]
    desired = []
    diff = diff_bindings(current, desired)
    assert len(diff["removed"]) == 1
    assert diff["removed"][0].action_path == "/Game/IA_Fire"


def test_legacy_binding_warning_only_when_enhanced_input():
    assert legacy_binding_warning(False) is None
    warn = legacy_binding_warning(True)
    assert warn and warn["error_code"] == "LEGACY_INPUT_PATH"
