from Content.Python.utils.blueprint_graph import (
    AUTOCAST_HINTS,
    PinDescriptor,
    evaluate_pin_compatibility,
    filter_graphs_by_kind,
    format_connection_diagnostics,
    merge_compile_diagnostics,
    normalize_graph_path,
    normalize_pin_descriptor,
    parse_node_selector,
    split_graph_path,
    stable_node_signature,
    suggest_autocast,
)


def test_normalize_graph_path_handles_separator_variants():
    assert normalize_graph_path("EventGraph/MyFunc") == "EventGraph/MyFunc"
    assert normalize_graph_path("EventGraph\\MyFunc") == "EventGraph/MyFunc"
    assert normalize_graph_path("EventGraph::MyFunc") == "EventGraph/MyFunc"
    assert normalize_graph_path("EventGraph.MyFunc") == "EventGraph/MyFunc"
    assert normalize_graph_path("/EventGraph/MyFunc/") == "EventGraph/MyFunc"
    assert normalize_graph_path("") == ""


def test_split_graph_path_returns_segments():
    assert split_graph_path("a/b/c") == ["a", "b", "c"]
    assert split_graph_path("") == []


def test_parse_node_selector_recognises_guid():
    parsed = parse_node_selector("EventGraph:" + "a" * 32)
    assert parsed["graph_path"] == "EventGraph"
    assert parsed["kind"] == "guid"


def test_parse_node_selector_handles_event_alias_and_no_graph():
    parsed = parse_node_selector("EventBeginPlay")
    assert parsed["graph_path"] == ""
    assert parsed["identifier"] == "EventBeginPlay"
    assert parsed["kind"] == "event"


def test_evaluate_pin_compatibility_direction_mismatch():
    src = PinDescriptor("A", "input", "int")
    tgt = PinDescriptor("B", "input", "int")
    out = evaluate_pin_compatibility(src, tgt)
    assert out["compatible"] is False
    assert out["error_code"] == "PIN_DIRECTION_MISMATCH"


def test_evaluate_pin_compatibility_exec_to_data_is_invalid():
    src = PinDescriptor("Then", "output", "exec")
    tgt = PinDescriptor("In", "input", "int")
    out = evaluate_pin_compatibility(src, tgt)
    assert out["compatible"] is False
    assert out["error_code"] == "PIN_EXEC_MISMATCH"


def test_evaluate_pin_compatibility_same_category_works():
    src = PinDescriptor("A", "output", "int")
    tgt = PinDescriptor("B", "input", "int")
    out = evaluate_pin_compatibility(src, tgt)
    assert out["compatible"] is True


def test_evaluate_pin_compatibility_real_to_double_compatible():
    src = PinDescriptor("A", "output", "float")
    tgt = PinDescriptor("B", "input", "double")
    out = evaluate_pin_compatibility(src, tgt)
    assert out["compatible"] is True


def test_evaluate_pin_compatibility_offers_autocast_when_known():
    src = PinDescriptor("A", "output", "int")
    tgt = PinDescriptor("B", "input", "string")
    out = evaluate_pin_compatibility(src, tgt)
    assert out["compatible"] is False
    assert out["error_code"] == "PIN_AUTOCAST_REQUIRED"
    assert out["autocast_suggestion"] == "Conv_IntToString"


def test_evaluate_pin_compatibility_object_subtype_mismatch():
    src = PinDescriptor("A", "output", "object", sub_category="Actor")
    tgt = PinDescriptor("B", "input", "object", sub_category="Pawn")
    out = evaluate_pin_compatibility(src, tgt)
    assert out["compatible"] is False
    assert out["error_code"] == "PIN_SUBTYPE_MISMATCH"


def test_evaluate_pin_compatibility_wildcard_passes():
    src = PinDescriptor("A", "output", "wildcard")
    tgt = PinDescriptor("B", "input", "int")
    out = evaluate_pin_compatibility(src, tgt)
    assert out["compatible"] is True


def test_evaluate_pin_compatibility_container_mismatch():
    src = PinDescriptor("A", "output", "int", container_type="array")
    tgt = PinDescriptor("B", "input", "int")
    out = evaluate_pin_compatibility(src, tgt)
    assert out["compatible"] is False
    assert out["error_code"] == "PIN_CONTAINER_MISMATCH"


def test_suggest_autocast_returns_known_hints():
    assert suggest_autocast("int", "string") == "Conv_IntToString"
    assert suggest_autocast("foo", "bar") is None
    assert ("int", "string") in AUTOCAST_HINTS


def test_format_connection_diagnostics_shape():
    out = format_connection_diagnostics(
        source_node="N1",
        source_pin="A",
        target_node="N2",
        target_pin="B",
        compatibility={"compatible": False, "reason": "x", "error_code": "PIN_X"},
    )
    assert out["source_node"] == "N1"
    assert out["compatible"] is False
    assert out["error_code"] == "PIN_X"


def test_filter_graphs_by_kind_returns_subset():
    graphs = [{"kind": "Function"}, {"kind": "Macro"}, {"kind": "function"}]
    assert filter_graphs_by_kind(graphs, "Function") == [{"kind": "Function"}, {"kind": "function"}]
    assert filter_graphs_by_kind(graphs, None) == graphs


def test_normalize_pin_descriptor_rejects_invalid():
    assert normalize_pin_descriptor(None) is None
    assert normalize_pin_descriptor({"direction": "input"}) is None
    assert normalize_pin_descriptor({"name": "P", "direction": "weird"}) is None
    assert normalize_pin_descriptor({"name": "P", "direction": "Input"}).direction == "input"


def test_stable_node_signature_prefers_guid():
    assert stable_node_signature({"guid": "abc"}) == "abc"
    assert stable_node_signature({"name": "N", "class": "K"}) == "K::N"
    assert stable_node_signature({"stable_name": "S", "guid": ""}) == "S"
    assert stable_node_signature({}) == ""


def test_merge_compile_diagnostics_collapses_payloads():
    merged = merge_compile_diagnostics([
        {"success": True, "warnings": [{"message": "w1"}], "errors": []},
        {"success": False, "errors": ["err"]},
    ])
    assert merged["success"] is False
    assert merged["warnings"] == [{"message": "w1"}]
    assert merged["errors"] == [{"message": "err"}]
