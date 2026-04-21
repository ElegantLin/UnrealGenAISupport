"""AnimBlueprint read/write handlers (P4 + P5).

Read methods return deterministic, JSON-friendly structures. Write methods
validate arguments in pure Python before delegating to
``UGenAnimationBlueprintUtils`` on the C++ side; the C++ layer is expected
to perform ``PostEditChange`` + ``CompileBlueprint`` + ``SavePackage``.
Every write response carries a :class:`MutationReport` so callers can see
exactly which assets were touched.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

try:
    from utils.anim_blueprint import (
        AnimBlueprintError,
        AnimBlueprintStructure,
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
    from utils.error_codes import (
        ANIM_BP_READ_FAILED,
        ANIM_BP_STATE_MACHINE_NOT_FOUND,
        ANIM_BP_STATE_NOT_FOUND,
        ANIM_BP_WRITE_FAILED,
        ASSET_NOT_FOUND,
        ASSET_VALIDATION_FAILED,
        GRAPH_NOT_FOUND,
        INVALID_PARAMETERS,
        MISSING_PARAMETERS,
        SAVE_FAILED,
        UNAVAILABLE_OUTSIDE_EDITOR,
    )
    from utils.mcp_response import err, ok
    from utils.observability import MutationReport, attach_report, build_bundle, persist_bundle
except ImportError:  # pragma: no cover - Unreal runtime path
    from Content.Python.utils.anim_blueprint import (
        AnimBlueprintError,
        AnimBlueprintStructure,
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
    from Content.Python.utils.error_codes import (
        ANIM_BP_READ_FAILED,
        ANIM_BP_STATE_MACHINE_NOT_FOUND,
        ANIM_BP_STATE_NOT_FOUND,
        ANIM_BP_WRITE_FAILED,
        ASSET_NOT_FOUND,
        ASSET_VALIDATION_FAILED,
        GRAPH_NOT_FOUND,
        INVALID_PARAMETERS,
        MISSING_PARAMETERS,
        SAVE_FAILED,
        UNAVAILABLE_OUTSIDE_EDITOR,
    )
    from Content.Python.utils.mcp_response import err, ok
    from Content.Python.utils.observability import MutationReport, attach_report, build_bundle, persist_bundle


def _get_unreal_module():
    try:
        import unreal  # type: ignore
    except ImportError:
        return None
    return unreal


def _get_utils():
    mod = _get_unreal_module()
    if mod is None:
        return None
    return getattr(mod, "GenAnimationBlueprintUtils", None)


def _call_cpp_json(method: str, *args) -> Dict[str, Any]:
    utils = _get_utils()
    if utils is None:
        return {"_unavailable": True, "error": "GenAnimationBlueprintUtils is not loaded outside the Unreal Editor."}
    fn = getattr(utils, method, None)
    if not callable(fn):
        return {"_unavailable": True, "error": "GenAnimationBlueprintUtils has no method " + method}
    try:
        raw = fn(*args)
    except Exception as exc:  # pragma: no cover - runtime only
        return {"_error": True, "error": str(exc)}
    text = raw if isinstance(raw, str) else str(raw or "")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}


def _unavailable(method: str, hint: str) -> Dict[str, Any]:
    return err(
        error_code=UNAVAILABLE_OUTSIDE_EDITOR,
        message=hint or f"{method} is unavailable outside the Unreal Editor",
        data={"cpp_method": method},
    )


def _validation_error(exc: AnimBlueprintError) -> Dict[str, Any]:
    return err(error_code=INVALID_PARAMETERS, message=str(exc))


def _require_anim_bp_path(command: Dict[str, Any]) -> Optional[str]:
    path = str(command.get("anim_blueprint_path") or command.get("path") or "").strip()
    return path or None


# ---------------------------------------------------------------------------
# P4 Read handlers ----------------------------------------------------------
# ---------------------------------------------------------------------------


def handle_get_anim_blueprint_structure(command: Dict[str, Any]) -> Dict[str, Any]:
    path = _require_anim_bp_path(command)
    if not path:
        return err(error_code=MISSING_PARAMETERS, message="anim_blueprint_path is required")

    result = _call_cpp_json("get_anim_blueprint_structure", path)
    if result.get("_unavailable"):
        return _unavailable("get_anim_blueprint_structure", result.get("error", ""))
    if result.get("_error"):
        return err(error_code=ANIM_BP_READ_FAILED, message=result.get("error", ""))
    if not result:
        return err(error_code=ASSET_NOT_FOUND, message=f"AnimBlueprint not found: {path}")

    structure = parse_structure(result)
    return ok("", data=structure.to_dict())


def handle_get_graph_nodes(command: Dict[str, Any]) -> Dict[str, Any]:
    path = _require_anim_bp_path(command)
    graph_path = str(command.get("graph_path") or "").strip()
    if not path or not graph_path:
        return err(error_code=MISSING_PARAMETERS, message="anim_blueprint_path and graph_path are required")
    try:
        parts = parse_graph_path(graph_path)
    except AnimBlueprintError as exc:
        return _validation_error(exc)

    result = _call_cpp_json("get_graph_nodes", path, "/".join(parts))
    if result.get("_unavailable"):
        return _unavailable("get_graph_nodes", result.get("error", ""))
    if result.get("_error"):
        return err(error_code=ANIM_BP_READ_FAILED, message=result.get("error", ""))
    if not result:
        return err(error_code=GRAPH_NOT_FOUND, message=f"Graph not found: {graph_path}")
    return ok("", data={
        "graph_path": "/".join(parts),
        "nodes": list(result.get("nodes") or []),
    })


def handle_get_graph_pins(command: Dict[str, Any]) -> Dict[str, Any]:
    path = _require_anim_bp_path(command)
    graph_path = str(command.get("graph_path") or "").strip()
    node_id = str(command.get("node_id") or "").strip()
    if not path or not graph_path or not node_id:
        return err(error_code=MISSING_PARAMETERS,
                   message="anim_blueprint_path, graph_path, and node_id are required")
    try:
        parts = parse_graph_path(graph_path)
    except AnimBlueprintError as exc:
        return _validation_error(exc)

    result = _call_cpp_json("get_graph_pins", path, "/".join(parts), node_id)
    if result.get("_unavailable"):
        return _unavailable("get_graph_pins", result.get("error", ""))
    if result.get("_error"):
        return err(error_code=ANIM_BP_READ_FAILED, message=result.get("error", ""))
    if not result:
        return err(error_code=GRAPH_NOT_FOUND, message=f"Node not found in {graph_path}: {node_id}")
    return ok("", data={
        "graph_path": "/".join(parts),
        "node_id": node_id,
        "pins": list(result.get("pins") or []),
    })


def handle_resolve_graph_by_path(command: Dict[str, Any]) -> Dict[str, Any]:
    path = _require_anim_bp_path(command)
    graph_path = str(command.get("graph_path") or "").strip()
    if not path or not graph_path:
        return err(error_code=MISSING_PARAMETERS, message="anim_blueprint_path and graph_path are required")
    try:
        parts = parse_graph_path(graph_path)
    except AnimBlueprintError as exc:
        return _validation_error(exc)

    result = _call_cpp_json("resolve_graph_by_path", path, "/".join(parts))
    if result.get("_unavailable"):
        return _unavailable("resolve_graph_by_path", result.get("error", ""))
    if result.get("_error"):
        return err(error_code=ANIM_BP_READ_FAILED, message=result.get("error", ""))
    if not result:
        return err(error_code=GRAPH_NOT_FOUND, message=f"Graph not found: {graph_path}")
    return ok("", data=result)


# ---------------------------------------------------------------------------
# P5 Write handlers ---------------------------------------------------------
# ---------------------------------------------------------------------------


def _begin_report(path: str) -> MutationReport:
    report = MutationReport()
    if path:
        report.changed_assets.append(path)
    return report


def _maybe_persist_failure_bundle(operation: str, command: Dict[str, Any], report: MutationReport) -> Optional[str]:
    """Best-effort diagnostic bundle. Returns saved path or None on failure.

    Errors are swallowed so the handler never fails because of a bundle
    write; the bundle is strictly supplementary diagnostic data.
    """
    try:
        bundle = build_bundle(
            operation=operation,
            request_payload=dict(command or {}),
            normalized_arguments=dict(command or {}),
            report=report,
        )
        return persist_bundle(bundle)
    except Exception:  # pragma: no cover - filesystem errors
        return None


def _finalize_write(
    method: str,
    result: Dict[str, Any],
    report: MutationReport,
    success_data: Dict[str, Any],
    *,
    verify_path: Optional[str] = None,
) -> Dict[str, Any]:
    if result.get("_unavailable"):
        return _unavailable(method, result.get("error", ""))
    if result.get("_error"):
        return attach_report(
            err(error_code=ANIM_BP_WRITE_FAILED, message=result.get("error", "")),
            report,
        )
    if not result.get("success", True):
        code = result.get("error_code") or ANIM_BP_WRITE_FAILED
        return attach_report(
            err(error_code=code, message=result.get("error") or result.get("message") or f"{method} failed",
                data=result),
            report,
        )

    compiled = bool(result.get("compiled"))
    saved = bool(result.get("saved"))
    if compiled and verify_path:
        report.compiled_assets.append(verify_path)
    if saved and verify_path:
        report.saved_assets.append(verify_path)
    report.warnings.extend(result.get("warnings") or [])

    verification = result.get("verification") or {}
    if verification:
        report.verification_checks.append(verification)

    return attach_report(ok("", data=success_data), report)


def handle_create_state_machine(command: Dict[str, Any]) -> Dict[str, Any]:
    try:
        sel = selector_state_machine(command)
    except AnimBlueprintError as exc:
        return _validation_error(exc)

    entry_state = str(command.get("entry_state") or "").strip() or None
    payload = {
        "state_machine": sel.state_machine,
        "entry_state": entry_state,
    }
    report = _begin_report(sel.anim_blueprint_path)
    result = _call_cpp_json("create_state_machine", sel.anim_blueprint_path, json.dumps(payload))
    return _finalize_write(
        "create_state_machine",
        result,
        report,
        success_data={"selector": sel.to_dict(), "entry_state": entry_state},
        verify_path=sel.anim_blueprint_path,
    )


def handle_create_state(command: Dict[str, Any]) -> Dict[str, Any]:
    try:
        sel = selector_state(command)
    except AnimBlueprintError as exc:
        return _validation_error(exc)

    kind = str(command.get("kind") or "State").strip() or "State"
    payload = {
        "state_machine": sel.state_machine,
        "state": sel.state,
        "kind": kind,
    }
    report = _begin_report(sel.anim_blueprint_path)
    result = _call_cpp_json("create_state", sel.anim_blueprint_path, json.dumps(payload))
    return _finalize_write(
        "create_state",
        result,
        report,
        success_data={"selector": sel.to_dict(), "kind": kind},
        verify_path=sel.anim_blueprint_path,
    )


def handle_create_transition(command: Dict[str, Any]) -> Dict[str, Any]:
    try:
        sel = selector_transition(command)
    except AnimBlueprintError as exc:
        return _validation_error(exc)

    rule_payload = command.get("rule")
    try:
        rule = normalize_transition_rule(rule_payload) if rule_payload is not None else normalize_transition_rule({})
    except AnimBlueprintError as exc:
        return _validation_error(exc)

    payload = {
        "state_machine": sel.state_machine,
        "from_state": sel.from_state,
        "to_state": sel.to_state,
        "rule": rule.to_dict(),
    }
    report = _begin_report(sel.anim_blueprint_path)
    result = _call_cpp_json("create_transition", sel.anim_blueprint_path, json.dumps(payload))
    return _finalize_write(
        "create_transition",
        result,
        report,
        success_data={"selector": sel.to_dict(), "rule": rule.to_dict()},
        verify_path=sel.anim_blueprint_path,
    )


def handle_set_transition_rule(command: Dict[str, Any]) -> Dict[str, Any]:
    try:
        sel = selector_transition(command)
        rule = normalize_transition_rule(command.get("rule"))
    except AnimBlueprintError as exc:
        return _validation_error(exc)

    payload = {
        "state_machine": sel.state_machine,
        "from_state": sel.from_state,
        "to_state": sel.to_state,
        "rule": rule.to_dict(),
    }
    report = _begin_report(sel.anim_blueprint_path)
    result = _call_cpp_json("set_transition_rule", sel.anim_blueprint_path, json.dumps(payload))
    return _finalize_write(
        "set_transition_rule",
        result,
        report,
        success_data={"selector": sel.to_dict(), "rule": rule.to_dict()},
        verify_path=sel.anim_blueprint_path,
    )


def handle_create_state_alias(command: Dict[str, Any]) -> Dict[str, Any]:
    try:
        sel = selector_state(command)
    except AnimBlueprintError as exc:
        return _validation_error(exc)

    raw_targets = command.get("aliased_states") or command.get("targets") or []
    if not isinstance(raw_targets, list):
        return err(error_code=INVALID_PARAMETERS, message="aliased_states must be a list")
    targets = [str(t).strip() for t in raw_targets if str(t).strip()]

    payload = {
        "state_machine": sel.state_machine,
        "alias_name": sel.state,
        "aliased_states": targets,
    }
    report = _begin_report(sel.anim_blueprint_path)
    result = _call_cpp_json("create_state_alias", sel.anim_blueprint_path, json.dumps(payload))
    return _finalize_write(
        "create_state_alias",
        result,
        report,
        success_data={"selector": sel.to_dict(), "aliased_states": targets},
        verify_path=sel.anim_blueprint_path,
    )


def handle_set_alias_targets(command: Dict[str, Any]) -> Dict[str, Any]:
    try:
        sel = selector_state(command)
    except AnimBlueprintError as exc:
        return _validation_error(exc)

    raw_targets = command.get("aliased_states") or command.get("targets") or []
    if not isinstance(raw_targets, list):
        return err(error_code=INVALID_PARAMETERS, message="aliased_states must be a list")
    targets = [str(t).strip() for t in raw_targets if str(t).strip()]
    if not targets:
        return err(error_code=ASSET_VALIDATION_FAILED,
                   message="aliased_states must contain at least one state name")

    payload = {
        "state_machine": sel.state_machine,
        "alias_name": sel.state,
        "aliased_states": targets,
    }
    report = _begin_report(sel.anim_blueprint_path)
    result = _call_cpp_json("set_alias_targets", sel.anim_blueprint_path, json.dumps(payload))
    return _finalize_write(
        "set_alias_targets",
        result,
        report,
        success_data={"selector": sel.to_dict(), "aliased_states": targets},
        verify_path=sel.anim_blueprint_path,
    )


def _write_state_asset(command: Dict[str, Any], cpp_method: str, kind: str) -> Dict[str, Any]:
    try:
        sel = selector_state(command)
        binding = normalize_state_asset_binding(command)
    except AnimBlueprintError as exc:
        return _validation_error(exc)

    payload = {
        "state_machine": sel.state_machine,
        "state": sel.state,
        "kind": kind,
        **binding.to_dict(),
    }
    report = _begin_report(sel.anim_blueprint_path)
    result = _call_cpp_json(cpp_method, sel.anim_blueprint_path, json.dumps(payload))
    return _finalize_write(
        cpp_method,
        result,
        report,
        success_data={"selector": sel.to_dict(), "binding": binding.to_dict()},
        verify_path=sel.anim_blueprint_path,
    )


def handle_set_state_sequence_asset(command: Dict[str, Any]) -> Dict[str, Any]:
    return _write_state_asset(command, "set_state_sequence_asset", kind="sequence")


def handle_set_state_blend_space_asset(command: Dict[str, Any]) -> Dict[str, Any]:
    return _write_state_asset(command, "set_state_blend_space_asset", kind="blend_space")


def handle_set_cached_pose_node(command: Dict[str, Any]) -> Dict[str, Any]:
    path = _require_anim_bp_path(command)
    pose_name = str(command.get("pose_name") or "").strip()
    source_node = str(command.get("source_node") or "").strip()
    if not path or not pose_name:
        return err(error_code=MISSING_PARAMETERS,
                   message="anim_blueprint_path and pose_name are required")

    payload = {"pose_name": pose_name, "source_node": source_node}
    report = _begin_report(path)
    result = _call_cpp_json("set_cached_pose_node", path, json.dumps(payload))
    return _finalize_write(
        "set_cached_pose_node",
        result,
        report,
        success_data={"anim_blueprint_path": path, "pose_name": pose_name, "source_node": source_node},
        verify_path=path,
    )


def handle_set_default_slot_chain(command: Dict[str, Any]) -> Dict[str, Any]:
    path = _require_anim_bp_path(command)
    slot_name = str(command.get("slot_name") or "DefaultSlot").strip() or "DefaultSlot"
    source_node = str(command.get("source_node") or "").strip()
    if not path:
        return err(error_code=MISSING_PARAMETERS, message="anim_blueprint_path is required")

    payload = {"slot_name": slot_name, "source_node": source_node}
    report = _begin_report(path)
    result = _call_cpp_json("set_default_slot_chain", path, json.dumps(payload))
    return _finalize_write(
        "set_default_slot_chain",
        result,
        report,
        success_data={"anim_blueprint_path": path, "slot_name": slot_name, "source_node": source_node},
        verify_path=path,
    )


def handle_set_apply_additive_chain(command: Dict[str, Any]) -> Dict[str, Any]:
    path = _require_anim_bp_path(command)
    base_node = str(command.get("base_node") or "").strip()
    additive_node = str(command.get("additive_node") or "").strip()
    alpha = command.get("alpha", 1.0)
    if not path or not base_node or not additive_node:
        return err(error_code=MISSING_PARAMETERS,
                   message="anim_blueprint_path, base_node, and additive_node are required")
    try:
        alpha_float = float(alpha)
    except (TypeError, ValueError):
        return err(error_code=INVALID_PARAMETERS, message="alpha must be numeric")
    if alpha_float < 0.0 or alpha_float > 1.0:
        return err(error_code=INVALID_PARAMETERS, message="alpha must be in [0, 1]")

    payload = {"base_node": base_node, "additive_node": additive_node, "alpha": alpha_float}
    report = _begin_report(path)
    result = _call_cpp_json("set_apply_additive_chain", path, json.dumps(payload))
    return _finalize_write(
        "set_apply_additive_chain",
        result,
        report,
        success_data={
            "anim_blueprint_path": path,
            "base_node": base_node,
            "additive_node": additive_node,
            "alpha": alpha_float,
        },
        verify_path=path,
    )
