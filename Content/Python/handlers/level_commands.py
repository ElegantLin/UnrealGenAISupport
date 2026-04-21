"""Level / level-instance / sublevel handlers.

Addresses postmortem gaps #1, #3, #4: we need a safe, structured surface for
creating levels from templates, creating and spawning level instances, and
adding levels to the world, while explicitly refusing the editor-only
``LevelStreamingLevelInstanceEditor`` path that crashed UE 5.4.4.

All handlers stay in pure Python and degrade gracefully when ``unreal``
isn't available (unit tests monkey-patch ``_get_unreal_module``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from utils.level_safety import (
        build_refusal_hint,
        is_forbidden_streaming_class,
        normalize_actor_names,
        resolve_template,
        validate_add_level_mode,
        validate_level_asset_path,
    )
    from utils.mcp_response import err, ok
except ImportError:  # pragma: no cover - pytest layout
    from Content.Python.utils.level_safety import (
        build_refusal_hint,
        is_forbidden_streaming_class,
        normalize_actor_names,
        resolve_template,
        validate_add_level_mode,
        validate_level_asset_path,
    )
    from Content.Python.utils.mcp_response import err, ok


# ---------------------------------------------------------------------------
# Unreal adapter helpers
# ---------------------------------------------------------------------------


def _get_unreal_module():
    try:
        import unreal  # type: ignore
    except ImportError:
        return None
    return unreal


def _unavailable(message: str) -> Dict[str, Any]:
    return err(
        error_code="UNAVAILABLE_OUTSIDE_EDITOR",
        message=message or "This command requires a running Unreal Editor.",
    )


def _get_editor_world(unreal_mod):
    """Return the active editor world using UE 5.3+ subsystem APIs first."""

    subsystems = getattr(unreal_mod, "get_editor_subsystem", None)
    subsystem_cls = getattr(unreal_mod, "UnrealEditorSubsystem", None)
    if callable(subsystems) and subsystem_cls is not None:
        try:
            sub = subsystems(subsystem_cls)
            if sub is not None:
                world = sub.get_editor_world()
                if world is not None:
                    return world
        except Exception:
            pass

    legacy = getattr(unreal_mod, "EditorLevelLibrary", None)
    if legacy is not None:
        try:
            return legacy.get_editor_world()
        except Exception:
            return None
    return None


def _get_actor_subsystem(unreal_mod):
    subsystems = getattr(unreal_mod, "get_editor_subsystem", None)
    actor_cls = getattr(unreal_mod, "EditorActorSubsystem", None)
    if callable(subsystems) and actor_cls is not None:
        try:
            return subsystems(actor_cls)
        except Exception:
            return None
    return None


def _get_level_editor_subsystem(unreal_mod):
    subsystems = getattr(unreal_mod, "get_editor_subsystem", None)
    cls = getattr(unreal_mod, "LevelEditorSubsystem", None)
    if callable(subsystems) and cls is not None:
        try:
            return subsystems(cls)
        except Exception:
            return None
    return None


def _find_actors_by_name(unreal_mod, names: List[str]):
    """Return the list of live editor actors matching any of ``names``."""

    wanted = {n for n in names}
    if not wanted:
        return []

    actor_subsystem = _get_actor_subsystem(unreal_mod)
    actors = []
    if actor_subsystem is not None:
        try:
            actors = list(actor_subsystem.get_all_level_actors() or [])
        except Exception:
            actors = []

    if not actors:
        world = _get_editor_world(unreal_mod)
        gs = getattr(unreal_mod, "GameplayStatics", None)
        actor_cls = getattr(unreal_mod, "Actor", None)
        if world is not None and gs is not None and actor_cls is not None:
            try:
                actors = list(gs.get_all_actors_of_class(world, actor_cls) or [])
            except Exception:
                actors = []

    resolved = []
    for actor in actors:
        try:
            label = str(actor.get_actor_label())
        except Exception:
            label = ""
        try:
            name = str(actor.get_name())
        except Exception:
            name = ""
        if label in wanted or name in wanted:
            resolved.append(actor)
    return resolved


def _vector(unreal_mod, value, default=(0.0, 0.0, 0.0)):
    try:
        x, y, z = (value if value is not None else default)
    except Exception:
        x, y, z = default
    ctor = getattr(unreal_mod, "Vector", None)
    if ctor is not None:
        try:
            return ctor(float(x), float(y), float(z))
        except Exception:
            pass
    return (float(x), float(y), float(z))


def _rotator(unreal_mod, value, default=(0.0, 0.0, 0.0)):
    try:
        p, y, r = (value if value is not None else default)
    except Exception:
        p, y, r = default
    ctor = getattr(unreal_mod, "Rotator", None)
    if ctor is not None:
        try:
            return ctor(float(p), float(y), float(r))
        except Exception:
            pass
    return (float(p), float(y), float(r))


def _transform(unreal_mod, location=None, rotation=None, scale=None):
    loc = _vector(unreal_mod, location, (0.0, 0.0, 0.0))
    rot = _rotator(unreal_mod, rotation, (0.0, 0.0, 0.0))
    scl = _vector(unreal_mod, scale, (1.0, 1.0, 1.0))
    ctor = getattr(unreal_mod, "Transform", None)
    if ctor is not None:
        try:
            return ctor(rot, loc, scl)
        except Exception:
            pass
    return {"location": loc, "rotation": rot, "scale": scl}


def _load_asset(unreal_mod, path: str):
    loader = getattr(unreal_mod, "EditorAssetLibrary", None)
    if loader is not None:
        try:
            return loader.load_asset(path)
        except Exception:
            return None
    return None


def _asset_exists(unreal_mod, path: str) -> bool:
    loader = getattr(unreal_mod, "EditorAssetLibrary", None)
    if loader is None:
        return False
    try:
        return bool(loader.does_asset_exist(path))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def handle_create_level_from_template(command: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new level from an engine template (``Basic``/``Empty`` etc.)."""

    level_path = str(command.get("level_path") or "").strip()
    template_raw = command.get("template") or command.get("template_name") or "Basic"

    if not level_path:
        return err(error_code="MISSING_PARAMETERS", message="level_path is required")
    if not level_path.startswith("/"):
        return err(
            error_code="INVALID_PARAMETERS",
            message=f"level_path must be an Unreal asset path starting with '/' (got {level_path!r})",
        )

    try:
        template_path = resolve_template(template_raw)
    except ValueError as exc:
        return err(error_code="LEVEL_TEMPLATE_NOT_FOUND", message=str(exc))

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("create_level_from_template requires a running Unreal Editor.")

    subsystem = _get_level_editor_subsystem(unreal_mod)
    try:
        if subsystem is not None and hasattr(subsystem, "new_level_from_template"):
            created = subsystem.new_level_from_template(level_path, template_path)
        else:
            legacy = getattr(unreal_mod, "EditorLevelLibrary", None)
            if legacy is None or not hasattr(legacy, "new_level_from_template"):
                return err(
                    error_code="LEVEL_OPERATION_FAILED",
                    message="Neither LevelEditorSubsystem nor EditorLevelLibrary expose new_level_from_template in this editor build.",
                )
            created = legacy.new_level_from_template(level_path, template_path)
    except Exception as exc:  # pragma: no cover - editor-only path
        return err(error_code="LEVEL_OPERATION_FAILED", message=str(exc))

    if not created:
        return err(
            error_code="LEVEL_OPERATION_FAILED",
            message=f"Failed to create level at {level_path} from template {template_path}.",
            data={"level_path": level_path, "template": template_path},
        )

    return ok(
        f"Created level {level_path}",
        data={"level_path": level_path, "template": template_path},
        changed_assets=[level_path],
    )


def handle_create_level_instance_from_selection(command: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap the editor ``Level > Create Level Instance`` action."""

    output_level_path = str(command.get("output_level_path") or "").strip()
    pivot_mode = str(command.get("pivot_mode") or "CenterMinZ").strip() or "CenterMinZ"
    external_actors = bool(command.get("external_actors", True))
    actor_names = normalize_actor_names(command.get("actor_names") or [])

    if not output_level_path:
        return err(error_code="MISSING_PARAMETERS", message="output_level_path is required")
    try:
        output_level_path = validate_level_asset_path(output_level_path)
    except ValueError as exc:
        return err(error_code="INVALID_PARAMETERS", message=str(exc))

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("create_level_instance_from_selection requires a running Unreal Editor.")

    # Resolve target actors: prefer explicit names, fall back to current selection.
    actors = []
    if actor_names:
        actors = _find_actors_by_name(unreal_mod, actor_names)
        missing = [n for n in actor_names if not any(
            (getattr(a, "get_actor_label", lambda: "")() == n) or (getattr(a, "get_name", lambda: "")() == n)
            for a in actors
        )]
        if missing:
            return err(
                error_code="ACTOR_NOT_FOUND",
                message=f"Could not resolve actors: {missing}",
                data={"missing_actors": missing},
            )
    else:
        actor_subsystem = _get_actor_subsystem(unreal_mod)
        if actor_subsystem is not None:
            try:
                actors = list(actor_subsystem.get_selected_level_actors() or [])
            except Exception:
                actors = []

    if not actors:
        return err(
            error_code="LEVEL_SELECTION_EMPTY",
            message="No actors supplied or selected to turn into a level instance.",
        )

    world = _get_editor_world(unreal_mod)
    level_instance_subsystem = None
    get_sub = getattr(unreal_mod, "get_editor_subsystem", None)
    ls_cls = getattr(unreal_mod, "LevelInstanceSubsystem", None)
    if callable(get_sub) and ls_cls is not None:
        try:
            level_instance_subsystem = get_sub(ls_cls)
        except Exception:
            level_instance_subsystem = None
    if level_instance_subsystem is None and world is not None:
        try:
            level_instance_subsystem = world.get_subsystem(ls_cls) if ls_cls is not None else None
        except Exception:
            level_instance_subsystem = None

    if level_instance_subsystem is None or not hasattr(level_instance_subsystem, "create_level_instance_from"):
        return err(
            error_code="LEVEL_OPERATION_FAILED",
            message="LevelInstanceSubsystem.create_level_instance_from is not available in this editor build.",
            warnings=[build_refusal_hint()],
        )

    try:
        new_actor = level_instance_subsystem.create_level_instance_from(
            actors,
            output_level_path,
            pivot_mode,
            external_actors,
        )
    except Exception as exc:  # pragma: no cover
        return err(error_code="LEVEL_OPERATION_FAILED", message=str(exc))

    if new_actor is None:
        return err(
            error_code="LEVEL_OPERATION_FAILED",
            message="create_level_instance_from returned no actor.",
        )

    actor_label = ""
    try:
        actor_label = str(new_actor.get_actor_label())
    except Exception:
        pass

    return ok(
        f"Created level instance {output_level_path}",
        data={
            "output_level_path": output_level_path,
            "pivot_mode": pivot_mode,
            "external_actors": external_actors,
            "source_actor_count": len(actors),
            "level_instance_actor": actor_label,
        },
        changed_assets=[output_level_path],
    )


def handle_spawn_level_instance(command: Dict[str, Any]) -> Dict[str, Any]:
    """Spawn a ``ALevelInstance`` actor bound to an existing level asset."""

    level_asset_path = str(command.get("level_asset_path") or "").strip()
    runtime_behavior = str(command.get("runtime_behavior") or "Embedded").strip() or "Embedded"
    actor_label = str(command.get("actor_label") or "").strip()

    if not level_asset_path:
        return err(error_code="MISSING_PARAMETERS", message="level_asset_path is required")
    try:
        level_asset_path = validate_level_asset_path(level_asset_path)
    except ValueError as exc:
        return err(error_code="INVALID_PARAMETERS", message=str(exc))

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("spawn_level_instance requires a running Unreal Editor.")

    if not _asset_exists(unreal_mod, level_asset_path):
        return err(
            error_code="ASSET_NOT_FOUND",
            message=f"Level asset not found: {level_asset_path}",
            data={"level_asset_path": level_asset_path},
        )

    location = command.get("location") or (0.0, 0.0, 0.0)
    rotation = command.get("rotation") or (0.0, 0.0, 0.0)
    scale = command.get("scale") or (1.0, 1.0, 1.0)
    transform = _transform(unreal_mod, location, rotation, scale)

    level_instance_cls = getattr(unreal_mod, "LevelInstance", None)
    if level_instance_cls is None:
        return err(
            error_code="LEVEL_OPERATION_FAILED",
            message="unreal.LevelInstance class is not available in this engine build.",
        )

    actor_subsystem = _get_actor_subsystem(unreal_mod)
    world = _get_editor_world(unreal_mod)

    actor = None
    try:
        if actor_subsystem is not None and hasattr(actor_subsystem, "spawn_actor_from_class"):
            actor = actor_subsystem.spawn_actor_from_class(level_instance_cls, transform.location if hasattr(transform, "location") else location, transform.rotation if hasattr(transform, "rotation") else rotation)
        else:
            legacy = getattr(unreal_mod, "EditorLevelLibrary", None)
            if legacy is not None:
                actor = legacy.spawn_actor_from_class(level_instance_cls, location, rotation)
    except Exception as exc:  # pragma: no cover
        return err(error_code="LEVEL_OPERATION_FAILED", message=str(exc))

    if actor is None:
        return err(
            error_code="LEVEL_OPERATION_FAILED",
            message="Failed to spawn LevelInstance actor in the current world.",
        )

    loaded_asset = _load_asset(unreal_mod, level_asset_path)
    if loaded_asset is None:
        return err(
            error_code="ASSET_NOT_FOUND",
            message=f"Unable to load level asset: {level_asset_path}",
        )

    # Bind the world asset and load it (API surface varies by UE version).
    try:
        setter = getattr(actor, "set_world_asset", None) or getattr(actor, "set_editor_property", None)
        if setter is None:
            return err(
                error_code="LEVEL_OPERATION_FAILED",
                message="Spawned LevelInstance actor exposes no world-asset setter.",
            )
        if setter is getattr(actor, "set_editor_property", None):
            setter("world_asset", loaded_asset)
        else:
            setter(loaded_asset)
    except Exception as exc:  # pragma: no cover
        return err(error_code="LEVEL_OPERATION_FAILED", message=str(exc))

    # Runtime behavior (optional enum).
    try:
        if hasattr(actor, "set_editor_property"):
            actor.set_editor_property("desired_runtime_behavior", runtime_behavior)
    except Exception:
        pass

    if actor_label:
        try:
            actor.set_actor_label(actor_label)
        except Exception:
            pass

    try:
        label = str(actor.get_actor_label())
    except Exception:
        label = ""

    return ok(
        f"Spawned level instance {label or level_asset_path}",
        data={
            "level_asset_path": level_asset_path,
            "actor_label": label,
            "runtime_behavior": runtime_behavior,
        },
    )


def handle_add_level_to_world(command: Dict[str, Any]) -> Dict[str, Any]:
    """Add an existing level asset to the current world safely.

    ``mode`` is one of ``sublevel`` | ``level_instance`` | ``packed_level``.
    Any attempt to use ``LevelStreamingLevelInstanceEditor`` (or equivalent)
    is refused up-front with a remediation hint.
    """

    level_path = str(command.get("level_path") or "").strip()
    mode_raw = command.get("mode") or "sublevel"
    streaming_class = str(command.get("streaming_class") or "").strip()
    location = command.get("location") or (0.0, 0.0, 0.0)
    rotation = command.get("rotation") or (0.0, 0.0, 0.0)

    if not level_path:
        return err(error_code="MISSING_PARAMETERS", message="level_path is required")
    try:
        level_path = validate_level_asset_path(level_path)
    except ValueError as exc:
        return err(error_code="INVALID_PARAMETERS", message=str(exc))

    try:
        mode = validate_add_level_mode(mode_raw)
    except ValueError as exc:
        return err(error_code="LEVEL_MODE_UNSUPPORTED", message=str(exc))

    # Guardrail: refuse the exact combination that crashed UE 5.4.4.
    if is_forbidden_streaming_class(streaming_class):
        return err(
            error_code="LEVEL_INSTANCE_UNSAFE",
            message=build_refusal_hint(),
            data={"requested_streaming_class": streaming_class},
            warnings=[build_refusal_hint()],
        )

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("add_level_to_world requires a running Unreal Editor.")

    if not _asset_exists(unreal_mod, level_path):
        return err(error_code="ASSET_NOT_FOUND", message=f"Level asset not found: {level_path}")

    if mode in ("level_instance", "packed_level"):
        # Delegate so both paths share validation & spawning.
        spawn_command = {
            "level_asset_path": level_path,
            "location": location,
            "rotation": rotation,
            "runtime_behavior": "Embedded" if mode == "level_instance" else "LevelStreaming",
            "actor_label": command.get("actor_label"),
        }
        response = handle_spawn_level_instance(spawn_command)
        if not response.get("success"):
            return response
        data = dict(response.get("data") or {})
        data["mode"] = mode
        return ok(response.get("message", "Added level to world."), data=data)

    # mode == "sublevel"
    level_utils = getattr(unreal_mod, "EditorLevelUtils", None)
    if level_utils is None or not hasattr(level_utils, "add_level_to_world"):
        return err(
            error_code="LEVEL_OPERATION_FAILED",
            message="EditorLevelUtils.add_level_to_world is not available in this editor build.",
        )

    streaming_cls = (
        getattr(unreal_mod, "LevelStreamingAlwaysLoaded", None)
        or getattr(unreal_mod, "LevelStreamingDynamic", None)
    )
    if streaming_cls is None:
        return err(
            error_code="LEVEL_OPERATION_FAILED",
            message="No supported LevelStreaming class is available for sublevel mode.",
        )

    world = _get_editor_world(unreal_mod)
    try:
        streamed = level_utils.add_level_to_world(world, level_path, streaming_cls)
    except Exception as exc:  # pragma: no cover
        return err(error_code="LEVEL_OPERATION_FAILED", message=str(exc))

    if streamed is None:
        return err(
            error_code="LEVEL_OPERATION_FAILED",
            message=f"Failed to add {level_path} as a sublevel.",
        )

    return ok(
        f"Added {level_path} as a sublevel.",
        data={
            "level_path": level_path,
            "mode": mode,
            "streaming_class": getattr(streaming_cls, "__name__", "LevelStreaming"),
        },
    )


def handle_list_level_instances(command: Dict[str, Any]) -> Dict[str, Any]:
    """List every ``ALevelInstance`` actor living in the current editor world."""

    del command
    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("list_level_instances requires a running Unreal Editor.")

    level_instance_cls = getattr(unreal_mod, "LevelInstance", None)
    actors = []

    actor_subsystem = _get_actor_subsystem(unreal_mod)
    if actor_subsystem is not None:
        try:
            actors = list(actor_subsystem.get_all_level_actors() or [])
        except Exception:
            actors = []

    if not actors:
        world = _get_editor_world(unreal_mod)
        gs = getattr(unreal_mod, "GameplayStatics", None)
        if world is not None and gs is not None and level_instance_cls is not None:
            try:
                actors = list(gs.get_all_actors_of_class(world, level_instance_cls) or [])
            except Exception:
                actors = []

    results: List[Dict[str, Any]] = []
    for actor in actors:
        if level_instance_cls is not None:
            try:
                if not isinstance(actor, level_instance_cls):
                    continue
            except Exception:
                pass
        entry: Dict[str, Any] = {}
        try:
            entry["name"] = str(actor.get_name())
        except Exception:
            entry["name"] = ""
        try:
            entry["label"] = str(actor.get_actor_label())
        except Exception:
            entry["label"] = ""
        # World asset path (API varies by engine version).
        asset_path = ""
        for getter in ("get_world_asset", "get_editor_property"):
            fn = getattr(actor, getter, None)
            if fn is None:
                continue
            try:
                value = fn("world_asset") if getter == "get_editor_property" else fn()
            except Exception:
                continue
            if value is not None:
                try:
                    asset_path = str(value.get_path_name())
                except Exception:
                    asset_path = str(value)
                break
        entry["world_asset"] = asset_path
        results.append(entry)

    return ok(
        f"Found {len(results)} level instance(s).",
        data={"level_instances": results, "count": len(results)},
    )
