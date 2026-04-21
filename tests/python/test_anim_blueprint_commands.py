import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

import json  # noqa: E402

from handlers import anim_blueprint_commands as ab  # noqa: E402


class _Utils:
    def __init__(self, responses):
        self._responses = dict(responses)

    def __getattr__(self, name):
        val = self._responses.get(name)
        if val is None:
            raise AttributeError(name)
        if callable(val):
            return val
        return lambda *args, **kwargs: val


def _install_utils(monkeypatch, responses):
    monkeypatch.setattr(ab, "_get_utils", lambda: _Utils(responses))


def test_get_structure_requires_path(monkeypatch):
    monkeypatch.setattr(ab, "_get_utils", lambda: object())
    resp = ab.handle_get_anim_blueprint_structure({})
    assert resp["success"] is False
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_get_structure_unavailable(monkeypatch):
    monkeypatch.setattr(ab, "_get_utils", lambda: None)
    resp = ab.handle_get_anim_blueprint_structure({"anim_blueprint_path": "/Game/ABP"})
    assert resp["success"] is False
    assert resp["error_code"] == "UNAVAILABLE_OUTSIDE_EDITOR"


def test_get_structure_parses_payload(monkeypatch):
    payload = json.dumps({
        "anim_blueprint_path": "/Game/ABP",
        "parent_class": "Character",
        "state_machines": [{
            "name": "Locomotion",
            "states": [{"name": "Idle"}, {"name": "Walk"}],
            "transitions": [{"from_state": "Idle", "to_state": "Walk"}],
        }],
    })
    _install_utils(monkeypatch, {"get_anim_blueprint_structure": payload})
    resp = ab.handle_get_anim_blueprint_structure({"anim_blueprint_path": "/Game/ABP"})
    assert resp["success"] is True
    assert resp["data"]["state_machines"][0]["name"] == "Locomotion"
    assert resp["data"]["schema_version"] == 1


def test_get_graph_nodes_validates_path(monkeypatch):
    monkeypatch.setattr(ab, "_get_utils", lambda: object())
    resp = ab.handle_get_graph_nodes({"anim_blueprint_path": "/Game/ABP", "graph_path": ""})
    assert resp["error_code"] == "MISSING_PARAMETERS"


def test_get_graph_nodes_missing_graph(monkeypatch):
    _install_utils(monkeypatch, {"get_graph_nodes": ""})
    resp = ab.handle_get_graph_nodes({"anim_blueprint_path": "/Game/ABP", "graph_path": "AnimGraph"})
    assert resp["error_code"] == "GRAPH_NOT_FOUND"


def test_get_graph_nodes_returns_nodes(monkeypatch):
    _install_utils(monkeypatch, {"get_graph_nodes": json.dumps({"nodes": [{"node_id": "n1"}]})})
    resp = ab.handle_get_graph_nodes({"anim_blueprint_path": "/Game/ABP", "graph_path": "AnimGraph"})
    assert resp["success"] is True
    assert resp["data"]["nodes"][0]["node_id"] == "n1"


def test_create_state_machine_ok_with_report(monkeypatch):
    _install_utils(monkeypatch, {"create_state_machine": json.dumps({"success": True, "compiled": True, "saved": True})})
    resp = ab.handle_create_state_machine({
        "anim_blueprint_path": "/Game/ABP",
        "state_machine": "Locomotion",
    })
    assert resp["success"] is True
    report = resp["data"]["mutation_report"]
    assert "/Game/ABP" in report["changed_assets"]
    assert "/Game/ABP" in report["compiled_assets"]
    assert "/Game/ABP" in report["saved_assets"]


def test_create_state_machine_validation(monkeypatch):
    monkeypatch.setattr(ab, "_get_utils", lambda: object())
    resp = ab.handle_create_state_machine({"anim_blueprint_path": "/Game/ABP"})
    assert resp["error_code"] == "INVALID_PARAMETERS"


def test_create_transition_invalid_rule(monkeypatch):
    monkeypatch.setattr(ab, "_get_utils", lambda: object())
    resp = ab.handle_create_transition({
        "anim_blueprint_path": "/Game/ABP",
        "state_machine": "Locomotion",
        "from_state": "Idle",
        "to_state": "Walk",
        "rule": {"kind": "bool_property"},
    })
    assert resp["error_code"] == "INVALID_PARAMETERS"


def test_create_transition_success(monkeypatch):
    _install_utils(monkeypatch, {"create_transition": json.dumps({"success": True, "compiled": True, "saved": True})})
    resp = ab.handle_create_transition({
        "anim_blueprint_path": "/Game/ABP",
        "state_machine": "Locomotion",
        "from_state": "Idle",
        "to_state": "Walk",
        "rule": {"kind": "bool_property", "property_name": "bMoving"},
    })
    assert resp["success"] is True
    assert resp["data"]["rule"]["property_name"] == "bMoving"


def test_set_state_sequence_asset_failure_preserves_report(monkeypatch):
    _install_utils(monkeypatch, {"set_state_sequence_asset": json.dumps({"success": False, "error": "not found", "error_code": "ASSET_NOT_FOUND"})})
    resp = ab.handle_set_state_sequence_asset({
        "anim_blueprint_path": "/Game/ABP",
        "state_machine": "Locomotion",
        "state": "Walk",
        "asset_path": "/Game/Missing",
    })
    assert resp["success"] is False
    assert resp["error_code"] == "ASSET_NOT_FOUND"
    assert "mutation_report" in resp
    assert "/Game/ABP" in resp["mutation_report"]["changed_assets"]


def test_set_apply_additive_alpha_bounds(monkeypatch):
    monkeypatch.setattr(ab, "_get_utils", lambda: object())
    resp = ab.handle_set_apply_additive_chain({
        "anim_blueprint_path": "/Game/ABP",
        "base_node": "A",
        "additive_node": "B",
        "alpha": 1.5,
    })
    assert resp["error_code"] == "INVALID_PARAMETERS"


def test_set_alias_targets_requires_targets(monkeypatch):
    monkeypatch.setattr(ab, "_get_utils", lambda: object())
    resp = ab.handle_set_alias_targets({
        "anim_blueprint_path": "/Game/ABP",
        "state_machine": "Locomotion",
        "state": "MovingAlias",
        "aliased_states": [],
    })
    assert resp["error_code"] == "ASSET_VALIDATION_FAILED"
