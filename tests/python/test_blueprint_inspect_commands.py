import json
import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _isolate_modules():
    sys.modules.pop("Content.Python.handlers.blueprint_inspect_commands", None)
    sys.modules.pop("unreal", None)
    yield
    sys.modules.pop("Content.Python.handlers.blueprint_inspect_commands", None)
    sys.modules.pop("unreal", None)


def _import(unreal_stub=None):
    if unreal_stub is not None:
        sys.modules["unreal"] = unreal_stub
    from Content.Python.handlers import blueprint_inspect_commands

    return blueprint_inspect_commands


def _make_unreal_stub(method_name, return_value):
    unreal_stub = types.ModuleType("unreal")

    class FakeBPUtils:
        pass

    setattr(FakeBPUtils, method_name, staticmethod(lambda *args: return_value))
    unreal_stub.GenBlueprintUtils = FakeBPUtils
    return unreal_stub


def test_get_graph_schema_returns_static_catalog_when_no_unreal():
    handlers = _import()
    response = handlers.handle_get_graph_schema({"blueprint_path": "/Game/BP"})
    assert response["success"] is True
    assert response["data"]["graphs"] == []
    assert "UbergraphPages" in response["data"]["supported_graph_types"]
    assert response["warnings"]


def test_get_graph_schema_uses_cpp_payload_when_present():
    payload = json.dumps({"graphs": [{"path": "EventGraph", "kind": "Ubergraph"}]})
    handlers = _import(_make_unreal_stub("get_all_graphs_json", payload))
    response = handlers.handle_get_graph_schema({"blueprint_path": "/Game/BP"})
    assert response["success"] is True
    assert response["data"]["graphs"][0]["path"] == "EventGraph"


def test_resolve_graph_by_path_requires_blueprint_path():
    handlers = _import()
    response = handlers.handle_resolve_graph_by_path({"graph_path": "EventGraph"})
    assert response["success"] is False
    assert response["error_code"] == "BLUEPRINT_PATH_REQUIRED"


def test_resolve_graph_by_path_normalizes_input():
    captured = {}

    unreal_stub = types.ModuleType("unreal")

    class FakeBPUtils:
        @staticmethod
        def resolve_graph_by_path(blueprint_path, graph_path):
            captured["blueprint_path"] = blueprint_path
            captured["graph_path"] = graph_path
            return json.dumps({"found": True, "graph_path": graph_path, "kind": "Function"})

    unreal_stub.GenBlueprintUtils = FakeBPUtils
    handlers = _import(unreal_stub)
    response = handlers.handle_resolve_graph_by_path(
        {"blueprint_path": "/Game/BP", "graph_path": "EventGraph\\MyFunc"}
    )
    assert captured["graph_path"] == "EventGraph/MyFunc"
    assert response["success"] is True
    assert response["data"]["graph_path"] == "EventGraph/MyFunc"


def test_resolve_graph_by_path_accepts_schema_object_path():
    captured = {}

    unreal_stub = types.ModuleType("unreal")

    class FakeBPUtils:
        @staticmethod
        def resolve_graph_by_path(blueprint_path, graph_path):
            captured["graph_path"] = graph_path
            return json.dumps({"found": True, "graph_path": graph_path, "kind": "Ubergraph"})

    unreal_stub.GenBlueprintUtils = FakeBPUtils
    handlers = _import(unreal_stub)
    response = handlers.handle_resolve_graph_by_path(
        {
            "blueprint_path": "/Game/BP.BP",
            "graph_path": "/Game/BP.BP:EventGraph",
        }
    )

    assert captured["graph_path"] == "EventGraph"
    assert response["success"] is True


def test_get_graph_nodes_surfaces_cpp_failure():
    payload = json.dumps({"success": False, "error_code": "GRAPH_NOT_FOUND", "error": "missing"})
    handlers = _import(_make_unreal_stub("get_graph_nodes_json", payload))
    response = handlers.handle_get_graph_nodes({"blueprint_path": "/Game/BP", "graph_path": "Missing"})
    assert response["success"] is False
    assert response["error_code"] == "GRAPH_NOT_FOUND"
    assert response["data"]["cpp_response"]["error"] == "missing"


def test_get_graph_pins_surfaces_cpp_failure():
    payload = json.dumps({"success": False, "error_code": "NODE_NOT_FOUND", "error": "missing node"})
    handlers = _import(_make_unreal_stub("get_graph_pins_json", payload))
    response = handlers.handle_get_graph_pins(
        {"blueprint_path": "/Game/BP", "graph_path": "EventGraph", "node_guid": "abc"}
    )
    assert response["success"] is False
    assert response["error_code"] == "NODE_NOT_FOUND"


def test_get_pin_compatibility_returns_envelope_with_diagnostics():
    handlers = _import()
    response = handlers.handle_get_pin_compatibility(
        {
            "source_node": "N1",
            "target_node": "N2",
            "source_pin": {"name": "A", "direction": "output", "category": "int"},
            "target_pin": {"name": "B", "direction": "input", "category": "string"},
        }
    )
    assert response["success"] is False
    assert response["error_code"] == "PIN_AUTOCAST_REQUIRED"
    assert response["data"]["autocast_suggestion"] == "Conv_IntToString"
    assert response["data"]["source_node"] == "N1"


def test_get_pin_compatibility_validates_descriptors():
    handlers = _import()
    response = handlers.handle_get_pin_compatibility(
        {"source_pin": {"name": "A"}, "target_pin": {"name": "B", "direction": "input"}}
    )
    assert response["success"] is False
    assert response["error_code"] == "PIN_DESCRIPTOR_INVALID"


def test_suggest_autocast_path_returns_known_suggestion():
    handlers = _import()
    response = handlers.handle_suggest_autocast_path(
        {"source_category": "int", "target_category": "string"}
    )
    assert response["success"] is True
    assert response["data"]["autocast_suggestion"] == "Conv_IntToString"


def test_suggest_autocast_path_returns_error_when_unknown():
    handlers = _import()
    response = handlers.handle_suggest_autocast_path(
        {"source_category": "foo", "target_category": "bar"}
    )
    assert response["success"] is False
    assert response["error_code"] == "AUTOCAST_UNAVAILABLE"


def test_compile_blueprint_with_diagnostics_surfaces_errors():
    payload = json.dumps(
        {
            "success": False,
            "warnings": [{"message": "w1"}],
            "errors": [{"message": "Boom", "node": "N42"}],
        }
    )
    handlers = _import(_make_unreal_stub("compile_blueprint_with_diagnostics", payload))
    response = handlers.handle_compile_blueprint_with_diagnostics(
        {"blueprint_path": "/Game/BP"}
    )
    assert response["success"] is False
    assert response["error_code"] == "BLUEPRINT_COMPILE_FAILED"
    assert response["data"]["errors"][0]["message"] == "Boom"
    assert response["warnings"] == ["w1"]


def test_resolve_node_by_selector_returns_parsed_when_no_unreal():
    handlers = _import()
    response = handlers.handle_resolve_node_by_selector(
        {"blueprint_path": "/Game/BP", "selector": "EventGraph:EventBeginPlay"}
    )
    assert response["success"] is True
    assert response["data"]["selector"]["graph_path"] == "EventGraph"
    assert response["data"]["selector"]["identifier"] == "EventBeginPlay"


def test_get_graph_pins_requires_node_guid():
    handlers = _import()
    response = handlers.handle_get_graph_pins(
        {"blueprint_path": "/Game/BP", "graph_path": "EventGraph"}
    )
    assert response["success"] is False
    assert response["error_code"] == "NODE_GUID_REQUIRED"
