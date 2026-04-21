"""Handlers for BlendSpace read/write (P3)."""

from __future__ import annotations

import json
from typing import Any, Dict, List

try:
    from utils.blend_space import (
        AxisConfig,
        BlendSample,
        BlendSpaceError,
        BlendSpaceInfo,
        build_reload_report,
        normalize_axis,
        normalize_sample,
        validate_samples,
    )
    from utils.mcp_response import err, ok
except ImportError:  # pragma: no cover
    from Content.Python.utils.blend_space import (
        AxisConfig,
        BlendSample,
        BlendSpaceError,
        BlendSpaceInfo,
        build_reload_report,
        normalize_axis,
        normalize_sample,
        validate_samples,
    )
    from Content.Python.utils.mcp_response import err, ok


def _get_unreal_module():
    try:
        import unreal  # type: ignore
    except ImportError:
        return None
    return unreal


def _get_anim_utils():
    mod = _get_unreal_module()
    if mod is None:
        return None
    return getattr(mod, "GenAnimationAssetUtils", None)


def _call_cpp_json(method: str, *args) -> Dict[str, Any]:
    utils = _get_anim_utils()
    if utils is None:
        return {"_unavailable": True, "error": "GenAnimationAssetUtils is not loaded outside the Unreal Editor."}
    fn = getattr(utils, method, None)
    if not callable(fn):
        return {"_unavailable": True, "error": "GenAnimationAssetUtils has no method " + method}
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


def _parse_info(payload: Dict[str, Any]) -> BlendSpaceInfo:
    axes: List[AxisConfig] = []
    for ax in payload.get("axes") or []:
        try:
            axes.append(normalize_axis(ax))
        except BlendSpaceError:
            axes.append(AxisConfig(
                name=str(ax.get("name") or ""),
                min_value=float(ax.get("min_value", 0.0) or 0.0),
                max_value=float(ax.get("max_value", 1.0) or 1.0),
            ))

    samples: List[BlendSample] = []
    for s in payload.get("samples") or []:
        try:
            samples.append(normalize_sample(s, axis_count=0))
        except BlendSpaceError:
            continue

    return BlendSpaceInfo(
        blend_space_path=str(payload.get("blend_space_path") or ""),
        skeleton_path=str(payload.get("skeleton_path") or ""),
        is_additive=bool(payload.get("is_additive")),
        axes=axes,
        samples=samples,
    )


def handle_get_blend_space_info(command: Dict[str, Any]) -> Dict[str, Any]:
    blend_space_path = str(command.get("blend_space_path") or "").strip()
    if not blend_space_path:
        return err(error_code="MISSING_PARAMETERS", message="blend_space_path is required")
    result = _call_cpp_json("get_blend_space_info", blend_space_path)
    if result.get("_unavailable"):
        return _unavailable("get_blend_space_info", result.get("error", ""))
    if result.get("_error"):
        return err(error_code="BLEND_SPACE_READ_FAILED", message=result.get("error", ""))
    return ok("", data=_parse_info(result).to_dict())


def handle_set_blend_space_axis(command: Dict[str, Any]) -> Dict[str, Any]:
    blend_space_path = str(command.get("blend_space_path") or "").strip()
    axis_index = command.get("axis_index")
    if not blend_space_path or axis_index is None:
        return err(error_code="MISSING_PARAMETERS", message="blend_space_path and axis_index are required")
    try:
        axis = normalize_axis(command.get("axis") or command.get("config") or {})
    except BlendSpaceError as exc:
        return err(error_code="INVALID_AXIS", message=str(exc))

    result = _call_cpp_json(
        "set_blend_space_axis",
        blend_space_path,
        int(axis_index),
        json.dumps(axis.to_dict()),
    )
    if result.get("_unavailable"):
        return _unavailable("set_blend_space_axis", result.get("error", ""))
    if result.get("_error") or not result.get("success", True):
        return err(error_code="BLEND_SPACE_WRITE_FAILED", message=result.get("error", "set_blend_space_axis failed"), data=result)
    return ok("", data={"axis": axis.to_dict(), "axis_index": int(axis_index)})


def handle_replace_blend_space_samples(command: Dict[str, Any]) -> Dict[str, Any]:
    blend_space_path = str(command.get("blend_space_path") or "").strip()
    if not blend_space_path:
        return err(error_code="MISSING_PARAMETERS", message="blend_space_path is required")

    before_raw = _call_cpp_json("get_blend_space_info", blend_space_path)
    if before_raw.get("_unavailable"):
        return _unavailable("get_blend_space_info", before_raw.get("error", ""))
    if before_raw.get("_error"):
        return err(error_code="BLEND_SPACE_READ_FAILED", message=before_raw.get("error", ""))
    before = _parse_info(before_raw)

    raw_samples = command.get("samples") or []
    if not isinstance(raw_samples, list):
        return err(error_code="INVALID_SAMPLES", message="samples must be a list")

    try:
        new_samples = [normalize_sample(s, axis_count=len(before.axes)) for s in raw_samples]
    except BlendSpaceError as exc:
        return err(error_code="INVALID_SAMPLES", message=str(exc))

    problems = validate_samples(new_samples, before.axes)
    if problems:
        return err(error_code="ASSET_VALIDATION_FAILED", message="BlendSpace samples are invalid",
            data={"problems": problems},
        )

    result = _call_cpp_json(
        "replace_blend_space_samples",
        blend_space_path,
        json.dumps({"samples": [s.to_dict() for s in new_samples]}),
    )
    if result.get("_unavailable"):
        return _unavailable("replace_blend_space_samples", result.get("error", ""))
    if result.get("_error") or not result.get("success", True):
        return err(error_code="BLEND_SPACE_WRITE_FAILED", message=result.get("error", "replace_blend_space_samples failed"),
            data=result,
        )

    after_raw = _call_cpp_json("get_blend_space_info", blend_space_path)
    if after_raw.get("_unavailable") or after_raw.get("_error"):
        return ok("", data={
                "applied": True,
                "sample_count": len(new_samples),
                "verification": build_reload_report(before, None),
            },
            warnings=[{"message": "BlendSpace saved but reload verification is unavailable."}],
        )
    after = _parse_info(after_raw)
    verification = build_reload_report(
        BlendSpaceInfo(
            blend_space_path=blend_space_path,
            axes=before.axes,
            samples=new_samples,
        ),
        after,
    )
    return ok("", data={
        "applied": True,
        "sample_count": len(new_samples),
        "verification": verification,
    })


def handle_set_blend_space_sample_animation(command: Dict[str, Any]) -> Dict[str, Any]:
    blend_space_path = str(command.get("blend_space_path") or "").strip()
    sample_index = command.get("sample_index")
    animation_path = str(command.get("animation_path") or "").strip()
    if not blend_space_path or sample_index is None or not animation_path:
        return err(error_code="MISSING_PARAMETERS", message="blend_space_path, sample_index, animation_path are required")

    result = _call_cpp_json(
        "set_blend_space_sample_animation",
        blend_space_path,
        int(sample_index),
        animation_path,
    )
    if result.get("_unavailable"):
        return _unavailable("set_blend_space_sample_animation", result.get("error", ""))
    if result.get("_error") or not result.get("success", True):
        return err(error_code="BLEND_SPACE_WRITE_FAILED", message=result.get("error", "set_blend_space_sample_animation failed"),
            data=result,
        )
    return ok("", data={
        "blend_space_path": blend_space_path,
        "sample_index": int(sample_index),
        "animation_path": animation_path,
    })
