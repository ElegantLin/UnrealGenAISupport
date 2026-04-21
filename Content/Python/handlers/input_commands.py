"""Handlers for Enhanced Input system work (P2).

The handlers validate / normalize via ``utils.input_mapping`` and then call
into ``unreal.GenEnhancedInputUtils`` static methods that operate on
``UInputAction`` / ``UInputMappingContext`` assets.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

try:
    from utils.input_mapping import (
        InputMappingError,
        build_binding,
        diff_bindings,
        legacy_binding_warning,
        normalize_key_name,
        normalize_modifier,
        normalize_trigger,
        normalize_value_type,
    )
    from utils.mcp_response import err, ok
except ImportError:  # pragma: no cover
    from Content.Python.utils.input_mapping import (
        InputMappingError,
        build_binding,
        diff_bindings,
        legacy_binding_warning,
        normalize_key_name,
        normalize_modifier,
        normalize_trigger,
        normalize_value_type,
    )
    from Content.Python.utils.mcp_response import err, ok


def _get_unreal_module():
    try:
        import unreal  # type: ignore
    except ImportError:
        return None
    return unreal


def _get_input_utils():
    mod = _get_unreal_module()
    if mod is None:
        return None
    return getattr(mod, "GenEnhancedInputUtils", None)


def _call_cpp_json(method: str, *args) -> Dict[str, Any]:
    utils = _get_input_utils()
    if utils is None:
        return {"_unavailable": True, "error": "GenEnhancedInputUtils is not loaded outside the Unreal Editor."}
    fn = getattr(utils, method, None)
    if not callable(fn):
        return {"_unavailable": True, "error": f"GenEnhancedInputUtils has no method '{method}'."}
    try:
        raw = fn(*args)
    except Exception as exc:  # pragma: no cover
        return {"_error": True, "error": str(exc)}
    text = raw if isinstance(raw, str) else str(raw or "")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}


def _unavailable(method: str, hint: str) -> Dict[str, Any]:
    return err(error_code="UNAVAILABLE_OUTSIDE_EDITOR", message=hint, data={"cpp_method": method})


def handle_create_input_action(command: Dict[str, Any]) -> Dict[str, Any]:
    name = str(command.get("name") or "").strip()
    save_path = str(command.get("save_path") or "/Game/Input").strip()
    if not name:
        return err(error_code="MISSING_PARAMETERS", message="name is required")
    try:
        value_type = normalize_value_type(command.get("value_type") or "Digital")
    except InputMappingError as exc:
        return err(error_code="INVALID_VALUE_TYPE", message=str(exc))
    description = str(command.get("description") or "")
    result = _call_cpp_json("create_input_action", name, save_path, value_type, description)
    if result.get("_unavailable"):
        return _unavailable("create_input_action", result.get("error", ""))
    if result.get("_error") or not result.get("success", True):
        return err(error_code="CREATE_INPUT_ACTION_FAILED", message=result.get("error", "Failed to create InputAction"), data=result)
    return ok("", data={"asset_path": result.get("asset_path"), "value_type": value_type})


def handle_create_input_mapping_context(command: Dict[str, Any]) -> Dict[str, Any]:
    name = str(command.get("name") or "").strip()
    save_path = str(command.get("save_path") or "/Game/Input").strip()
    if not name:
        return err(error_code="MISSING_PARAMETERS", message="name is required")
    result = _call_cpp_json("create_input_mapping_context", name, save_path)
    if result.get("_unavailable"):
        return _unavailable("create_input_mapping_context", result.get("error", ""))
    if result.get("_error") or not result.get("success", True):
        return err(error_code="CREATE_INPUT_CONTEXT_FAILED", message=result.get("error", "Failed to create InputMappingContext"),
            data=result,
        )
    return ok("", data={"asset_path": result.get("asset_path")})


def handle_map_enhanced_input_action(command: Dict[str, Any]) -> Dict[str, Any]:
    context_path = str(command.get("context_path") or "").strip()
    action_path = str(command.get("action_path") or "").strip()
    if not context_path or not action_path:
        return err(error_code="MISSING_PARAMETERS", message="context_path and action_path are required")

    try:
        binding = build_binding(
            action_path=action_path,
            key=command.get("key"),
            triggers=command.get("triggers") or (),
            modifiers=command.get("modifiers") or (),
        )
    except InputMappingError as exc:
        return err(error_code="INVALID_BINDING", message=str(exc))

    result = _call_cpp_json(
        "map_enhanced_input_action",
        context_path,
        binding.action_path,
        binding.key,
        json.dumps(list(binding.triggers)),
        json.dumps(list(binding.modifiers)),
    )
    if result.get("_unavailable"):
        return _unavailable("map_enhanced_input_action", result.get("error", ""))
    if result.get("_error") or not result.get("success", True):
        return err(error_code="INPUT_MAPPING_FAILED", message=result.get("error", "Failed to map Enhanced Input action"),
            data={"binding": binding.to_dict(), **result},
        )
    return ok("", data={"binding": binding.to_dict(), "context_path": context_path})


def handle_list_input_mappings(command: Dict[str, Any]) -> Dict[str, Any]:
    context_path = str(command.get("context_path") or "").strip()
    if not context_path:
        return err(error_code="MISSING_PARAMETERS", message="context_path is required")
    result = _call_cpp_json("list_input_mappings", context_path)
    if result.get("_unavailable"):
        return _unavailable("list_input_mappings", result.get("error", ""))
    if result.get("_error"):
        return err(error_code="LIST_INPUT_MAPPINGS_FAILED", message=result.get("error", ""))
    mappings = result.get("mappings", [])
    if not isinstance(mappings, list):
        mappings = []
    return ok("", data={"context_path": context_path, "mappings": mappings})


def handle_legacy_input_binding_warning(command: Dict[str, Any]) -> Dict[str, Any]:
    """Advisory endpoint exposed to wrapper ``add_input_binding`` callers."""

    project_uses_ei = bool(command.get("project_uses_enhanced_input", True))
    warn = legacy_binding_warning(project_uses_ei)
    if not warn:
        return ok("", data={"warning": None})
    return ok("", data={"warning": warn}, warnings=[warn])
