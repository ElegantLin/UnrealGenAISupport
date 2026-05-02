"""Project-settings editing (postmortem gap #7).

Provides a small, stable API to read/write common project settings.  Each
setting is addressed by the *class path* of its settings object plus the
property name (e.g. ``/Script/Engine.RendererSettings`` +
``bDefaultFeatureAutoExposure``).  A curated ``set_rendering_defaults``
handler wraps the most frequently requested toggles.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

try:
    from utils.mcp_response import err, ok
except ImportError:  # pragma: no cover
    from Content.Python.utils.mcp_response import err, ok


def _get_unreal_module():
    try:
        import unreal  # type: ignore
    except ImportError:
        return None
    return unreal


def _unavailable(message: str) -> Dict[str, Any]:
    return err(error_code="UNAVAILABLE_OUTSIDE_EDITOR", message=message)


# Curated rendering setting map used by ``set_rendering_defaults``.
RENDERING_SETTING_MAP = {
    "auto_exposure": ("/Script/Engine.RendererSettings", "bDefaultFeatureAutoExposure"),
    "motion_blur": ("/Script/Engine.RendererSettings", "bDefaultFeatureMotionBlur"),
    "bloom": ("/Script/Engine.RendererSettings", "bDefaultFeatureBloom"),
    "ambient_occlusion": ("/Script/Engine.RendererSettings", "bDefaultFeatureAmbientOcclusion"),
    "lens_flares": ("/Script/Engine.RendererSettings", "bDefaultFeatureLensFlare"),
    "anti_aliasing": ("/Script/Engine.RendererSettings", "DefaultFeatureAntiAliasing"),
}


def _resolve_settings_cdo(unreal_mod, class_path: str):
    """Return the CDO for a settings class identified by path or short name."""

    class_path = str(class_path or "").strip()
    if not class_path:
        return None, "settings_class is required"

    candidates = [class_path]
    if not class_path.startswith("/Script/"):
        candidates.append(f"/Script/Engine.{class_path}")
        candidates.append(f"/Script/UnrealEd.{class_path}")

    load_class = getattr(unreal_mod, "load_class", None)
    if not callable(load_class):
        return None, "unreal.load_class is not available in this engine build."

    cls = None
    for candidate in candidates:
        try:
            cls = load_class(None, candidate)
        except Exception:
            cls = None
        if cls is not None:
            break

    # Fallback: attribute on unreal namespace (e.g. unreal.RendererSettings).
    if cls is None and "." in class_path:
        short = class_path.rsplit(".", 1)[-1]
        cls = getattr(unreal_mod, short, None)
    elif cls is None:
        cls = getattr(unreal_mod, class_path, None)

    if cls is None:
        return None, f"Could not resolve settings class {class_path!r}."

    cdo = None
    get_cdo = getattr(unreal_mod, "get_default_object", None)
    if callable(get_cdo):
        try:
            cdo = get_cdo(cls)
        except Exception:
            cdo = None
    if cdo is None:
        getter = getattr(cls, "get_default_object", None)
        if callable(getter):
            try:
                cdo = getter()
            except Exception as exc:  # pragma: no cover
                return None, f"Failed to get settings CDO: {exc}"
    if cdo is None:
        return None, "Settings class exposes no default object."
    return cdo, None


def handle_set_project_setting(command: Dict[str, Any]) -> Dict[str, Any]:
    """Set a single property on a project settings class."""

    settings_class = str(command.get("settings_class") or "").strip()
    key = str(command.get("key") or command.get("property") or "").strip()
    if "value" not in command:
        return err(error_code="MISSING_PARAMETERS", message="value is required")
    value = command.get("value")
    if not settings_class:
        return err(error_code="MISSING_PARAMETERS", message="settings_class is required")
    if not key:
        return err(error_code="MISSING_PARAMETERS", message="key is required")

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("set_project_setting requires a running Unreal Editor.")

    cdo, error = _resolve_settings_cdo(unreal_mod, settings_class)
    if cdo is None:
        return err(error_code="PROJECT_SETTING_NOT_FOUND", message=error or "")

    try:
        cdo.set_editor_property(key, value)
    except Exception as exc:
        return err(
            error_code="PROJECT_SETTING_FAILED",
            message=f"Failed to set {settings_class}.{key}: {exc}",
        )

    persisted = bool(command.get("save_config", True))
    if persisted:
        save_cfg = getattr(cdo, "save_config", None)
        if callable(save_cfg):
            try:
                save_cfg()
            except Exception:
                persisted = False
        else:
            persisted = False

    return ok(
        f"Set {settings_class}.{key}.",
        data={
            "settings_class": settings_class,
            "key": key,
            "value": value,
            "saved_to_config": persisted,
        },
    )


def handle_set_rendering_defaults(command: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the common rendering toggles used for automation setups."""

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("set_rendering_defaults requires a running Unreal Editor.")

    changes: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for short_name, (settings_class, key) in RENDERING_SETTING_MAP.items():
        if short_name not in command:
            continue
        value = command.get(short_name)
        if value is None:
            continue
        response = handle_set_project_setting({
            "settings_class": settings_class,
            "key": key,
            "value": value,
            "save_config": command.get("save_config", True),
        })
        entry = {
            "name": short_name,
            "settings_class": settings_class,
            "key": key,
            "value": value,
            "ok": bool(response.get("success")),
        }
        if not response.get("success"):
            entry["error"] = response.get("error")
            warnings.append(f"{short_name}: {response.get('error')}")
        changes.append(entry)

    if not changes:
        return ok(
            "No rendering defaults specified.",
            data={"changes": []},
            warnings=["Call this tool with at least one rendering toggle (auto_exposure, motion_blur, ...)."],
        )

    ok_changes = [c for c in changes if c["ok"]]
    return ok(
        f"Applied {len(ok_changes)}/{len(changes)} rendering defaults.",
        data={"changes": changes},
        warnings=warnings,
    )
