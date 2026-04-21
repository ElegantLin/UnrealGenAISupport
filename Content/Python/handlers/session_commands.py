"""Handlers for the editor session snapshot / restore flow (P1.5).

The snapshot itself is normalized by ``utils.session_state``.  These handlers
wrap the calls into ``unreal.GenEditorSessionUtils`` (a C++
``UBlueprintFunctionLibrary``) and degrade to a structured
``UNAVAILABLE_OUTSIDE_EDITOR`` envelope when we are running under pytest.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

try:
    from utils.mcp_response import err, ok
    from utils.session_state import (
        DEFAULT_RESTORE_POLICY,
        RESTORE_POLICIES,
        build_restore_report,
        classify_restore_targets,
        filter_by_policy,
        normalize_snapshot,
    )
except ImportError:  # pragma: no cover - pytest layout
    from Content.Python.utils.mcp_response import err, ok
    from Content.Python.utils.session_state import (
        DEFAULT_RESTORE_POLICY,
        RESTORE_POLICIES,
        build_restore_report,
        classify_restore_targets,
        filter_by_policy,
        normalize_snapshot,
    )


def _get_unreal_module():
    try:
        import unreal  # type: ignore
    except ImportError:
        return None
    return unreal


def _get_session_utils():
    mod = _get_unreal_module()
    if mod is None:
        return None
    return getattr(mod, "GenEditorSessionUtils", None)


def _call_cpp_json(method_name: str, *args) -> Dict[str, Any]:
    utils = _get_session_utils()
    if utils is None:
        return {"_unavailable": True, "error": "GenEditorSessionUtils is not loaded outside the Unreal Editor."}
    method: Optional[Callable[..., Any]] = getattr(utils, method_name, None)
    if not callable(method):
        return {"_unavailable": True, "error": f"GenEditorSessionUtils has no method '{method_name}'."}
    try:
        raw = method(*args)
    except Exception as exc:  # pragma: no cover - defensive
        return {"_error": True, "error": str(exc)}
    if not isinstance(raw, str):
        raw = str(raw or "")
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}


def _unavailable_envelope(method_name: str, hint: str) -> Dict[str, Any]:
    return err(error_code="UNAVAILABLE_OUTSIDE_EDITOR", message=hint,
        data={"cpp_method": method_name},
    )


def handle_capture_editor_session(command: Dict[str, Any]) -> Dict[str, Any]:
    """Capture the current editor state.  The heavy lifting is done in C++."""

    result = _call_cpp_json("capture_session_json")
    if result.get("_unavailable"):
        return _unavailable_envelope("capture_session_json", result.get("error", ""))
    if result.get("_error"):
        return err(error_code="SESSION_CAPTURE_FAILED", message=result.get("error", ""))

    snapshot = normalize_snapshot(result)
    return ok("", data=snapshot.to_dict())


def handle_restore_editor_session(command: Dict[str, Any]) -> Dict[str, Any]:
    """Restore the last captured session per the requested policy."""

    policy = str(command.get("policy") or DEFAULT_RESTORE_POLICY).lower()
    if policy not in RESTORE_POLICIES:
        return err(error_code="INVALID_RESTORE_POLICY", message=f"policy must be one of {RESTORE_POLICIES}",
            data={"received": policy},
        )

    override = command.get("snapshot")
    if override is not None:
        if not isinstance(override, dict):
            return err(error_code="INVALID_SNAPSHOT", message="snapshot must be an object when provided")
        snapshot_payload = override
    else:
        loaded = _call_cpp_json("load_last_session_json")
        if loaded.get("_unavailable"):
            return _unavailable_envelope("load_last_session_json", loaded.get("error", ""))
        if loaded.get("_error"):
            return err(error_code="SESSION_LOAD_FAILED", message=loaded.get("error", ""))
        if not loaded:
            return err(error_code="SESSION_MISSING", message="No saved editor session found.")
        snapshot_payload = loaded

    snapshot = filter_by_policy(normalize_snapshot(snapshot_payload), policy)
    restorable, skipped = classify_restore_targets(snapshot)

    restored: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    session_utils = _get_session_utils()

    if session_utils is None and policy != "none":
        return _unavailable_envelope(
            "apply_restore_plan",
            "GenEditorSessionUtils is not loaded; restore plan computed but not applied.",
        )

    for entry in restorable:
        apply_result = _call_cpp_json(
            "open_asset_for_restore",
            entry.asset_path,
            bool(entry.is_primary),
        )
        if apply_result.get("_unavailable") or apply_result.get("_error"):
            failed.append(
                {
                    "asset_path": entry.asset_path,
                    "reason": apply_result.get("error") or "restore call failed",
                }
            )
            continue
        if apply_result.get("success", True):
            restored.append({"asset_path": entry.asset_path, "is_primary": bool(entry.is_primary)})
        else:
            failed.append(
                {
                    "asset_path": entry.asset_path,
                    "reason": apply_result.get("error") or apply_result.get("reason") or "unknown",
                }
            )

    if snapshot.primary_asset_path:
        _call_cpp_json("bring_asset_to_front", snapshot.primary_asset_path)
    if snapshot.active_graph_path and snapshot.primary_asset_path:
        _call_cpp_json("focus_graph", snapshot.primary_asset_path, snapshot.active_graph_path)

    report = build_restore_report(
        restored=restored,
        failed=failed,
        skipped=[{"asset_path": e.asset_path, "asset_class": e.asset_class} for e in skipped],
    )
    report["policy"] = policy
    report["active_graph_path"] = snapshot.active_graph_path
    return ok("", data=report)


def handle_save_editor_session(command: Dict[str, Any]) -> Dict[str, Any]:
    """Persist the current (or provided) session to ``Saved/MCP/LastEditorSession.json``."""

    payload = command.get("snapshot")
    if payload is None:
        captured = _call_cpp_json("capture_session_json")
        if captured.get("_unavailable"):
            return _unavailable_envelope("capture_session_json", captured.get("error", ""))
        if captured.get("_error"):
            return err(error_code="SESSION_CAPTURE_FAILED", message=captured.get("error", ""))
        payload = captured

    snapshot = normalize_snapshot(payload if isinstance(payload, dict) else {})
    saved = _call_cpp_json("save_session_json", json.dumps(snapshot.to_dict()))
    if saved.get("_unavailable"):
        return _unavailable_envelope("save_session_json", saved.get("error", ""))
    if saved.get("_error") or not saved.get("success", True):
        return err(error_code="SESSION_SAVE_FAILED", message=saved.get("error", "Failed to persist session snapshot."))
    return ok("", data={"saved": True, "snapshot": snapshot.to_dict()})


def handle_open_asset(command: Dict[str, Any]) -> Dict[str, Any]:
    asset_path = str(command.get("asset_path") or "").strip()
    if not asset_path:
        return err(error_code="MISSING_PARAMETERS", message="asset_path is required")
    result = _call_cpp_json("open_asset_for_restore", asset_path, bool(command.get("primary", False)))
    if result.get("_unavailable"):
        return _unavailable_envelope("open_asset_for_restore", result.get("error", ""))
    if result.get("_error") or not result.get("success", True):
        return err(error_code="OPEN_ASSET_FAILED", message=result.get("error", "Unable to open asset"), data=result)
    return ok("", data={"asset_path": asset_path})


def handle_bring_asset_to_front(command: Dict[str, Any]) -> Dict[str, Any]:
    asset_path = str(command.get("asset_path") or "").strip()
    if not asset_path:
        return err(error_code="MISSING_PARAMETERS", message="asset_path is required")
    result = _call_cpp_json("bring_asset_to_front", asset_path)
    if result.get("_unavailable"):
        return _unavailable_envelope("bring_asset_to_front", result.get("error", ""))
    if result.get("_error"):
        return err(error_code="FOCUS_ASSET_FAILED", message=result.get("error", ""))
    return ok("", data={"asset_path": asset_path})


def handle_focus_graph(command: Dict[str, Any]) -> Dict[str, Any]:
    asset_path = str(command.get("asset_path") or command.get("blueprint_path") or "").strip()
    graph_path = str(command.get("graph_path") or "").strip()
    if not asset_path or not graph_path:
        return err(error_code="MISSING_PARAMETERS", message="asset_path and graph_path are required")
    result = _call_cpp_json("focus_graph", asset_path, graph_path)
    if result.get("_unavailable"):
        return _unavailable_envelope("focus_graph", result.get("error", ""))
    if result.get("_error") or not result.get("success", True):
        return err(error_code="FOCUS_GRAPH_FAILED", message=result.get("error", "Unable to focus graph"), data=result)
    return ok("", data={"asset_path": asset_path, "graph_path": graph_path})


def handle_focus_node(command: Dict[str, Any]) -> Dict[str, Any]:
    asset_path = str(command.get("asset_path") or command.get("blueprint_path") or "").strip()
    graph_path = str(command.get("graph_path") or "").strip()
    node_guid = str(command.get("node_guid") or "").strip()
    if not (asset_path and graph_path and node_guid):
        return err(error_code="MISSING_PARAMETERS", message="asset_path, graph_path, node_guid are required")
    result = _call_cpp_json("focus_node", asset_path, graph_path, node_guid)
    if result.get("_unavailable"):
        return _unavailable_envelope("focus_node", result.get("error", ""))
    if result.get("_error") or not result.get("success", True):
        return err(error_code="FOCUS_NODE_FAILED", message=result.get("error", "Unable to focus node"), data=result)
    return ok("", data={"asset_path": asset_path, "graph_path": graph_path, "node_guid": node_guid})


def handle_select_actor(command: Dict[str, Any]) -> Dict[str, Any]:
    actor_label = str(command.get("actor_label") or command.get("actor_path") or "").strip()
    if not actor_label:
        return err(error_code="MISSING_PARAMETERS", message="actor_label or actor_path is required")
    result = _call_cpp_json("select_actor", actor_label)
    if result.get("_unavailable"):
        return _unavailable_envelope("select_actor", result.get("error", ""))
    if result.get("_error") or not result.get("success", True):
        return err(error_code="SELECT_ACTOR_FAILED", message=result.get("error", "Unable to select actor"), data=result)
    return ok("", data={"actor": actor_label})
