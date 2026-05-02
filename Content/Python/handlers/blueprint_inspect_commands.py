"""Handlers for Blueprint graph inspection (P1.25).

These handlers do *no* Editor work themselves; they validate input, normalize
graph paths and selectors, then call into ``UGenBlueprintUtils`` C++ helpers
that return JSON.  When the C++ helpers are unavailable (e.g. running outside
the Editor), the handlers return a structured ``UNAVAILABLE_OUTSIDE_EDITOR``
error envelope so the contract stays predictable.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

try:  # pragma: no cover - import shape depends on environment
    from utils.blueprint_graph import (
        collect_supported_graph_types,
        evaluate_pin_compatibility,
        format_connection_diagnostics,
        normalize_graph_path,
        normalize_pin_descriptor,
        parse_node_selector,
        suggest_autocast,
    )
    from utils.mcp_response import err, ok
except ImportError:  # pragma: no cover
    from Content.Python.utils.blueprint_graph import (
        collect_supported_graph_types,
        evaluate_pin_compatibility,
        format_connection_diagnostics,
        normalize_graph_path,
        normalize_pin_descriptor,
        parse_node_selector,
        suggest_autocast,
    )
    from Content.Python.utils.mcp_response import err, ok


def _get_unreal_module():
    try:
        import unreal  # type: ignore
    except ImportError:
        return None
    return unreal


def _get_blueprint_utils():
    unreal_module = _get_unreal_module()
    if unreal_module is None:
        return None
    return getattr(unreal_module, "GenBlueprintUtils", None)


def _call_cpp_json(method_name: str, *args) -> Dict[str, Any]:
    """Invoke a ``GenBlueprintUtils`` static method and parse its JSON output."""

    utils = _get_blueprint_utils()
    if utils is None:
        return {
            "_unavailable": True,
            "error": "GenBlueprintUtils is not loaded outside the Unreal Editor.",
        }
    method: Optional[Callable[..., Any]] = getattr(utils, method_name, None)
    if not callable(method):
        return {
            "_unavailable": True,
            "error": f"GenBlueprintUtils has no callable method '{method_name}'.",
        }

    try:
        raw = method(*args)
    except Exception as exc:
        return {"_error": True, "error": str(exc)}

    text = raw if isinstance(raw, str) else str(raw or "")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}


def _missing_param(name: str) -> Dict[str, Any]:
    return err(
        f"Missing required parameter: {name}",
        error_code=f"{name.upper()}_REQUIRED",
    )


def _unavailable_response(detail: str = "") -> Dict[str, Any]:
    return err(
        detail or "Blueprint inspection is only available inside the Unreal Editor.",
        error_code="UNAVAILABLE_OUTSIDE_EDITOR",
    )


def _cpp_failure_response(
    payload: Dict[str, Any],
    *,
    default_code: str,
    default_message: str,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    details = dict(data or {})
    details["cpp_response"] = payload
    return err(
        payload.get("error") or default_message,
        error_code=payload.get("error_code") or default_code,
        data=details,
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def handle_get_graph_schema(command: Dict[str, Any]) -> Dict[str, Any]:
    blueprint_path = str(command.get("blueprint_path", "")).strip()
    if not blueprint_path:
        return _missing_param("blueprint_path")

    payload = _call_cpp_json("get_all_graphs_json", blueprint_path)
    if payload.get("_unavailable"):
        return ok(
            "Returning the static, Editor-independent graph schema catalog.",
            data={
                "blueprint_path": blueprint_path,
                "supported_graph_types": collect_supported_graph_types(),
                "graphs": [],
            },
            warnings=[payload.get("error", "Editor unavailable.")],
        )
    if payload.get("_error"):
        return err(
            payload["error"],
            error_code="GRAPH_SCHEMA_FAILED",
            data={"blueprint_path": blueprint_path},
        )

    return ok(
        "Graph schema retrieved.",
        data={
            "blueprint_path": blueprint_path,
            "supported_graph_types": collect_supported_graph_types(),
            "graphs": payload.get("graphs", []),
        },
    )


def handle_resolve_graph_by_path(command: Dict[str, Any]) -> Dict[str, Any]:
    blueprint_path = str(command.get("blueprint_path", "")).strip()
    graph_path = normalize_graph_path(command.get("graph_path", ""))

    if not blueprint_path:
        return _missing_param("blueprint_path")
    if not graph_path:
        return _missing_param("graph_path")

    payload = _call_cpp_json("resolve_graph_by_path", blueprint_path, graph_path)
    if payload.get("_unavailable"):
        return _unavailable_response(payload.get("error", ""))
    if payload.get("_error"):
        return err(payload["error"], error_code="RESOLVE_GRAPH_FAILED")
    if not payload.get("found", True):
        return err(
            f"Graph not found: {graph_path}",
            error_code="GRAPH_NOT_FOUND",
            data={"blueprint_path": blueprint_path, "graph_path": graph_path},
        )
    return ok("Graph resolved.", data=payload)


def handle_get_graph_nodes(command: Dict[str, Any]) -> Dict[str, Any]:
    blueprint_path = str(command.get("blueprint_path", "")).strip()
    graph_path = normalize_graph_path(command.get("graph_path", ""))
    if not blueprint_path:
        return _missing_param("blueprint_path")
    if not graph_path:
        return _missing_param("graph_path")

    payload = _call_cpp_json("get_graph_nodes_json", blueprint_path, graph_path)
    if payload.get("_unavailable"):
        return _unavailable_response(payload.get("error", ""))
    if payload.get("_error"):
        return err(payload["error"], error_code="GRAPH_NODES_FAILED")
    if payload.get("success") is False:
        return _cpp_failure_response(
            payload,
            default_code="GRAPH_NODES_FAILED",
            default_message=f"Graph nodes lookup failed: {graph_path}",
            data={"blueprint_path": blueprint_path, "graph_path": graph_path},
        )

    nodes = payload.get("nodes") if isinstance(payload, dict) else []
    return ok(
        "Graph nodes retrieved.",
        data={
            "blueprint_path": blueprint_path,
            "graph_path": graph_path,
            "nodes": nodes or [],
        },
    )


def handle_get_graph_pins(command: Dict[str, Any]) -> Dict[str, Any]:
    blueprint_path = str(command.get("blueprint_path", "")).strip()
    graph_path = normalize_graph_path(command.get("graph_path", ""))
    node_guid = str(command.get("node_guid", "")).strip()
    if not blueprint_path:
        return _missing_param("blueprint_path")
    if not graph_path:
        return _missing_param("graph_path")
    if not node_guid:
        return _missing_param("node_guid")

    payload = _call_cpp_json("get_graph_pins_json", blueprint_path, graph_path, node_guid)
    if payload.get("_unavailable"):
        return _unavailable_response(payload.get("error", ""))
    if payload.get("_error"):
        return err(payload["error"], error_code="GRAPH_PINS_FAILED")
    if payload.get("success") is False:
        return _cpp_failure_response(
            payload,
            default_code="GRAPH_PINS_FAILED",
            default_message=f"Graph pins lookup failed: {graph_path}/{node_guid}",
            data={
                "blueprint_path": blueprint_path,
                "graph_path": graph_path,
                "node_guid": node_guid,
            },
        )

    return ok(
        "Graph pins retrieved.",
        data={
            "blueprint_path": blueprint_path,
            "graph_path": graph_path,
            "node_guid": node_guid,
            "pins": payload.get("pins", []),
        },
    )


def handle_resolve_node_by_selector(command: Dict[str, Any]) -> Dict[str, Any]:
    blueprint_path = str(command.get("blueprint_path", "")).strip()
    selector_text = str(command.get("selector", "")).strip()
    if not blueprint_path:
        return _missing_param("blueprint_path")
    if not selector_text:
        return _missing_param("selector")

    parsed = parse_node_selector(selector_text)
    payload = _call_cpp_json(
        "resolve_node_by_selector",
        blueprint_path,
        parsed["graph_path"],
        parsed["identifier"],
        parsed["kind"],
    )
    if payload.get("_unavailable"):
        return ok(
            "Returning the parsed selector only; Editor lookup unavailable.",
            data={"blueprint_path": blueprint_path, "selector": parsed, "match": None},
            warnings=[payload.get("error", "Editor unavailable.")],
        )
    if payload.get("_error"):
        return err(payload["error"], error_code="NODE_RESOLVE_FAILED")
    if not payload.get("found", False):
        return err(
            f"Node not found for selector: {selector_text}",
            error_code="NODE_NOT_FOUND",
            data={"selector": parsed},
        )
    return ok("Node resolved.", data={"selector": parsed, "match": payload})


def handle_get_pin_compatibility(command: Dict[str, Any]) -> Dict[str, Any]:
    source = normalize_pin_descriptor(command.get("source_pin"))
    target = normalize_pin_descriptor(command.get("target_pin"))

    if source is None or target is None:
        return err(
            "Both source_pin and target_pin must include 'name', 'direction' and 'category'.",
            error_code="PIN_DESCRIPTOR_INVALID",
        )

    compatibility = evaluate_pin_compatibility(source, target)
    diagnostics = format_connection_diagnostics(
        source_node=str(command.get("source_node", "")),
        source_pin=source.name,
        target_node=str(command.get("target_node", "")),
        target_pin=target.name,
        compatibility=compatibility,
    )

    if compatibility["compatible"]:
        return ok("Pins are compatible.", data=diagnostics)
    return err(
        compatibility["reason"],
        error_code=compatibility["error_code"] or "PIN_INCOMPATIBLE",
        data=diagnostics,
    )


def handle_suggest_autocast_path(command: Dict[str, Any]) -> Dict[str, Any]:
    source_category = command.get("source_category", "")
    target_category = command.get("target_category", "")
    suggestion = suggest_autocast(source_category, target_category)
    if suggestion is None:
        return err(
            (
                f"No autocast path is known from {source_category!r} to {target_category!r}."
            ),
            error_code="AUTOCAST_UNAVAILABLE",
            data={
                "source_category": source_category,
                "target_category": target_category,
                "autocast_suggestion": None,
            },
        )
    return ok(
        "Autocast suggestion available.",
        data={
            "source_category": source_category,
            "target_category": target_category,
            "autocast_suggestion": suggestion,
        },
    )


def handle_compile_blueprint_with_diagnostics(command: Dict[str, Any]) -> Dict[str, Any]:
    """Compile a Blueprint and return structured diagnostics."""

    blueprint_path = str(command.get("blueprint_path", "")).strip()
    if not blueprint_path:
        return _missing_param("blueprint_path")

    payload = _call_cpp_json("compile_blueprint_with_diagnostics", blueprint_path)
    if payload.get("_unavailable"):
        return _unavailable_response(payload.get("error", ""))
    if payload.get("_error"):
        return err(payload["error"], error_code="COMPILE_DIAGNOSTICS_FAILED")

    success = bool(payload.get("success", False))
    warnings: List[str] = []
    for warning in payload.get("warnings", []) or []:
        if isinstance(warning, dict) and warning.get("message"):
            warnings.append(str(warning["message"]))
        elif isinstance(warning, str):
            warnings.append(warning)

    data = {
        "blueprint_path": blueprint_path,
        "compiled": success,
        "warnings": payload.get("warnings", []),
        "errors": payload.get("errors", []),
        "graph_context": payload.get("graph_context", []),
    }
    if success:
        return ok("Blueprint compiled.", data=data, warnings=warnings)
    return err(
        "Blueprint failed to compile.",
        error_code="BLUEPRINT_COMPILE_FAILED",
        data=data,
        warnings=warnings,
    )
