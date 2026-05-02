import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

import pytest  # noqa: E402

from utils.anim_blueprint import (  # noqa: E402
    AnimBlueprintError,
    diff_structures,
    normalize_play_mode,
    normalize_state_asset_binding,
    normalize_transition_rule,
    parse_graph_path,
    parse_structure,
    selector_state,
    selector_state_machine,
    selector_transition,
)


def test_parse_graph_path_strips_and_splits():
    assert parse_graph_path("/AnimGraph/Locomotion/Walk/") == ["AnimGraph", "Locomotion", "Walk"]
    assert parse_graph_path("AnimGraph//Locomotion") == ["AnimGraph", "Locomotion"]


def test_parse_graph_path_accepts_full_uobject_graph_path():
    assert parse_graph_path("/Game/Characters/ABP_Manny.ABP_Manny:AnimGraph") == ["AnimGraph"]
    assert parse_graph_path("/Game/Characters/ABP_Manny.ABP_Manny:AnimGraph/Locomotion") == [
        "AnimGraph",
        "Locomotion",
    ]


def test_parse_graph_path_rejects_empty():
    with pytest.raises(AnimBlueprintError):
        parse_graph_path("")
    with pytest.raises(AnimBlueprintError):
        parse_graph_path("///")


def test_selectors_require_fields():
    with pytest.raises(AnimBlueprintError):
        selector_state_machine({})
    with pytest.raises(AnimBlueprintError):
        selector_state({"anim_blueprint_path": "/Game/ABP", "state_machine": "Locomotion"})
    with pytest.raises(AnimBlueprintError):
        selector_transition({"anim_blueprint_path": "/Game/ABP", "state_machine": "Locomotion", "from_state": "Idle"})


def test_selector_transition_builds():
    sel = selector_transition({
        "anim_blueprint_path": "/Game/ABP",
        "state_machine": "Locomotion",
        "from_state": "Idle",
        "to_state": "Walk",
    })
    assert sel.to_dict()["from_state"] == "Idle"


def test_normalize_transition_rule_default_is_always():
    rule = normalize_transition_rule(None)
    assert rule.kind == "always"


def test_normalize_transition_rule_bool_property_required():
    with pytest.raises(AnimBlueprintError):
        normalize_transition_rule({"kind": "bool_property"})
    rule = normalize_transition_rule({"kind": "bool_property", "property_name": "bCrouched"})
    assert rule.property_name == "bCrouched"


def test_normalize_transition_rule_expression_rejects_semicolon():
    with pytest.raises(AnimBlueprintError):
        normalize_transition_rule({"kind": "expression", "expression": "A; B"})


def test_normalize_transition_rule_blend_time_must_be_positive():
    with pytest.raises(AnimBlueprintError):
        normalize_transition_rule({"kind": "always", "blend_time": -0.1})


def test_normalize_play_mode_case_insensitive():
    assert normalize_play_mode("loop") == "Loop"
    assert normalize_play_mode("FREEZE") == "Freeze"
    with pytest.raises(AnimBlueprintError):
        normalize_play_mode("bogus")


def test_normalize_state_asset_binding_validates_play_rate():
    with pytest.raises(AnimBlueprintError):
        normalize_state_asset_binding({"asset_path": "/Game/A", "play_rate": 0})
    binding = normalize_state_asset_binding({"asset_path": "/Game/A"})
    assert binding.play_rate == 1.0
    assert binding.play_mode == "Loop"


def test_parse_structure_handles_missing_fields():
    structure = parse_structure({"anim_blueprint_path": "/Game/ABP"})
    assert structure.state_machines == []
    assert structure.parent_class == ""


def test_parse_structure_and_diff():
    before = parse_structure({
        "anim_blueprint_path": "/Game/ABP",
        "state_machines": [{
            "name": "Locomotion",
            "entry_state": "Idle",
            "states": [{"name": "Idle"}, {"name": "Walk"}],
            "transitions": [{"from_state": "Idle", "to_state": "Walk"}],
        }],
    })
    after = parse_structure({
        "anim_blueprint_path": "/Game/ABP",
        "state_machines": [{
            "name": "Locomotion",
            "states": [{"name": "Idle"}, {"name": "Walk"}, {"name": "Run"}],
            "transitions": [
                {"from_state": "Idle", "to_state": "Walk"},
                {"from_state": "Walk", "to_state": "Run"},
            ],
        }],
    })
    diff = diff_structures(before, after)
    assert "Locomotion:Run" in diff["states_added"]
    assert "Locomotion:Walk->Run" in diff["transitions_added"]
    assert diff["state_machines_added"] == []
