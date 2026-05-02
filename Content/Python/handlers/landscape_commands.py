"""Landscape creation helpers (postmortem gap #2).

The UE Python API for landscape creation is limited: ``ALandscape`` can be
spawned as an actor, but a usable landscape requires an ``ULandscapeInfo``,
a component layout, and a configured material.  The implementation wraps the
public pieces that *are* exposed (``ALandscape.import_`` / setting landscape
material / spawning actor) and returns ``LANDSCAPE_UNAVAILABLE`` when the
engine build does not expose them so callers can fall back gracefully.
"""

from __future__ import annotations

from typing import Any, Dict

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


def _get_actor_subsystem(unreal_mod):
    subsystems = getattr(unreal_mod, "get_editor_subsystem", None)
    cls = getattr(unreal_mod, "EditorActorSubsystem", None)
    if callable(subsystems) and cls is not None:
        try:
            return subsystems(cls)
        except Exception:
            return None
    return None


def _load_material(unreal_mod, path: str):
    loader = getattr(unreal_mod, "EditorAssetLibrary", None)
    if loader is None:
        return None
    try:
        return loader.load_asset(path)
    except Exception:
        return None


def _actor_class_name(actor) -> str:
    getter = getattr(actor, "get_class", None)
    if callable(getter):
        try:
            cls = getter()
            name_getter = getattr(cls, "get_name", None)
            if callable(name_getter):
                return str(name_getter())
            return str(cls or "")
        except Exception:
            pass
    return type(actor).__name__


def _destroy_actor(actor) -> None:
    destroy = getattr(actor, "destroy_actor", None)
    if callable(destroy):
        try:
            destroy()
        except Exception:
            pass


def handle_create_landscape(command: Dict[str, Any]) -> Dict[str, Any]:
    """Spawn a landscape actor at a given transform with an optional material.

    ``size`` is used to compute a ``scale`` vector that makes the default
    landscape component roughly the requested extent on X/Y.  Full
    section-count / quads-per-component configuration is not exposed via
    Python, so the handler records whatever settings the engine accepted and
    returns an ``actor_label`` that callers can reuse with other tools.
    """

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("create_landscape requires a running Unreal Editor.")

    landscape_cls = getattr(unreal_mod, "Landscape", None)
    if landscape_cls is None:
        return err(
            error_code="LANDSCAPE_UNAVAILABLE",
            message="unreal.Landscape is not available in this engine build. Landscape creation requires the editor Landscape module.",
        )

    actor_subsystem = _get_actor_subsystem(unreal_mod)
    if actor_subsystem is None:
        return err(
            error_code="LANDSCAPE_OPERATION_FAILED",
            message="EditorActorSubsystem is not available; cannot spawn landscape.",
        )

    location = command.get("location") or (0.0, 0.0, 0.0)
    rotation = command.get("rotation") or (0.0, 0.0, 0.0)
    scale = command.get("scale")
    size = command.get("size")
    material_path = str(command.get("material_path") or "").strip()
    actor_label = str(command.get("actor_label") or "MCP_Landscape").strip()

    # Derive a uniform scale from size hint if explicit scale not provided.
    if scale is None and size is not None:
        try:
            sx, sy = (float(size[0]), float(size[1]))
        except Exception:
            sx, sy = 1.0, 1.0
        # Default landscape extents are 63 quads * 128 units.
        default_extent = 63.0 * 128.0
        scale = (sx / default_extent or 1.0, sy / default_extent or 1.0, 1.0)
    if scale is None:
        scale = (1.0, 1.0, 1.0)

    try:
        vector_ctor = getattr(unreal_mod, "Vector")
        rotator_ctor = getattr(unreal_mod, "Rotator")
        loc = vector_ctor(float(location[0]), float(location[1]), float(location[2]))
        rot = rotator_ctor(float(rotation[0]), float(rotation[1]), float(rotation[2]))
        scl = vector_ctor(float(scale[0]), float(scale[1]), float(scale[2]))
    except Exception as exc:
        return err(error_code="INVALID_PARAMETERS", message=f"Invalid transform: {exc}")

    try:
        actor = actor_subsystem.spawn_actor_from_class(landscape_cls, loc, rot)
    except Exception as exc:  # pragma: no cover
        return err(error_code="LANDSCAPE_OPERATION_FAILED", message=str(exc))

    if actor is None:
        return err(
            error_code="LANDSCAPE_OPERATION_FAILED",
            message="Failed to spawn Landscape actor in the current world.",
        )

    actor_class_name = _actor_class_name(actor)
    if actor_class_name == "LandscapePlaceholder":
        _destroy_actor(actor)
        return err(
            error_code="LANDSCAPE_UNAVAILABLE",
            message=(
                "This editor build spawned LandscapePlaceholder instead of a usable Landscape actor. "
                "Landscape creation requires the editor Landscape mode/import path, which is not exposed "
                "through this Python API."
            ),
            data={"spawned_class": actor_class_name},
        )

    try:
        actor.set_actor_scale3d(scl)
    except Exception:
        try:
            actor.set_editor_property("actor_scale3d", scl)
        except Exception:
            pass

    if actor_label:
        try:
            actor.set_actor_label(actor_label)
        except Exception:
            pass

    warnings = []
    material_asset = None
    if material_path:
        material_asset = _load_material(unreal_mod, material_path)
        if material_asset is None:
            warnings.append(f"Could not load material {material_path}; landscape spawned without it.")
        else:
            try:
                actor.set_editor_property("landscape_material", material_asset)
            except Exception as exc:
                warnings.append(f"Failed to assign landscape_material: {exc}")

    try:
        resolved_label = str(actor.get_actor_label())
    except Exception:
        resolved_label = actor_label

    return ok(
        f"Spawned landscape {resolved_label}.",
        data={
            "actor_label": resolved_label,
            "material_path": material_path or None,
            "scale": [float(scale[0]), float(scale[1]), float(scale[2])],
        },
        warnings=warnings,
    )


def handle_set_landscape_material(command: Dict[str, Any]) -> Dict[str, Any]:
    """Assign a material to an existing landscape actor by label or name."""

    actor_ref = str(command.get("actor_name") or command.get("actor_label") or "").strip()
    material_path = str(command.get("material_path") or "").strip()

    if not actor_ref:
        return err(error_code="MISSING_PARAMETERS", message="actor_name (or actor_label) is required")
    if not material_path:
        return err(error_code="MISSING_PARAMETERS", message="material_path is required")

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("set_landscape_material requires a running Unreal Editor.")

    actor_subsystem = _get_actor_subsystem(unreal_mod)
    if actor_subsystem is None:
        return err(
            error_code="LANDSCAPE_OPERATION_FAILED",
            message="EditorActorSubsystem is not available.",
        )

    try:
        actors = list(actor_subsystem.get_all_level_actors() or [])
    except Exception as exc:
        return err(error_code="LANDSCAPE_OPERATION_FAILED", message=str(exc))

    target = None
    for candidate in actors:
        label = name = ""
        try:
            label = str(candidate.get_actor_label())
        except Exception:
            pass
        try:
            name = str(candidate.get_name())
        except Exception:
            pass
        if actor_ref in (label, name):
            target = candidate
            break

    if target is None:
        return err(
            error_code="ACTOR_NOT_FOUND",
            message=f"No landscape actor with label/name {actor_ref!r} was found.",
        )

    landscape_cls = getattr(unreal_mod, "Landscape", None)
    if landscape_cls is not None:
        try:
            if not isinstance(target, landscape_cls):
                return err(
                    error_code="LANDSCAPE_OPERATION_FAILED",
                    message=f"Actor {actor_ref} is not a Landscape (got {type(target).__name__}).",
                )
        except Exception:
            pass

    material_asset = _load_material(unreal_mod, material_path)
    if material_asset is None:
        return err(
            error_code="MATERIAL_NOT_FOUND",
            message=f"Failed to load material {material_path}.",
        )

    try:
        target.set_editor_property("landscape_material", material_asset)
    except Exception as exc:
        return err(error_code="LANDSCAPE_OPERATION_FAILED", message=str(exc))

    return ok(
        f"Set landscape material on {actor_ref}.",
        data={"actor_name": actor_ref, "material_path": material_path},
    )
