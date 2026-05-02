"""Batch actor editing handlers (postmortem gap #8).

Kept in a dedicated module so it can be unit-tested without dragging in the
unconditional ``import unreal`` that ``actor_commands.py`` requires.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

try:
    from utils.mcp_response import err, ok
except ImportError:  # pragma: no cover - pytest layout
    from Content.Python.utils.mcp_response import err, ok



# ---------------------------------------------------------------------------
# Batch actor operations (postmortem gap #8)
# ---------------------------------------------------------------------------


def _get_unreal_module():
    try:
        import unreal as unreal_mod  # type: ignore
    except ImportError:
        return None
    return unreal_mod


def _unavailable(message: str) -> Dict[str, Any]:
    return err(error_code="UNAVAILABLE_OUTSIDE_EDITOR", message=message)


def _get_actor_subsystem(unreal_mod):
    sub_getter = getattr(unreal_mod, "get_editor_subsystem", None)
    cls = getattr(unreal_mod, "EditorActorSubsystem", None)
    if callable(sub_getter) and cls is not None:
        try:
            return sub_getter(cls)
        except Exception:
            return None
    return None


def _all_level_actors(unreal_mod) -> List[Any]:
    subsystem = _get_actor_subsystem(unreal_mod)
    if subsystem is not None:
        try:
            return list(subsystem.get_all_level_actors() or [])
        except Exception:
            pass
    gs = getattr(unreal_mod, "GameplayStatics", None)
    actor_cls = getattr(unreal_mod, "Actor", None)
    legacy = getattr(unreal_mod, "EditorLevelLibrary", None)
    world = None
    sub_getter = getattr(unreal_mod, "get_editor_subsystem", None)
    eds_cls = getattr(unreal_mod, "UnrealEditorSubsystem", None)
    if callable(sub_getter) and eds_cls is not None:
        try:
            sub = sub_getter(eds_cls)
            if sub is not None:
                world = sub.get_editor_world()
        except Exception:
            world = None
    if world is None and legacy is not None:
        try:
            world = legacy.get_editor_world()
        except Exception:
            world = None
    if world is None or gs is None or actor_cls is None:
        return []
    try:
        return list(gs.get_all_actors_of_class(world, actor_cls) or [])
    except Exception:
        return []


def _actor_matches(actor, ref: str) -> bool:
    try:
        if str(actor.get_actor_label()) == ref:
            return True
    except Exception:
        pass
    try:
        if str(actor.get_name()) == ref:
            return True
    except Exception:
        pass
    return False


def _find_actors(unreal_mod, names: List[str]) -> Tuple[List[Any], List[str]]:
    cleaned = [n.strip() for n in (names or []) if str(n or "").strip()]
    all_actors = _all_level_actors(unreal_mod)
    resolved: List[Any] = []
    missing: List[str] = []
    for ref in cleaned:
        match = next((a for a in all_actors if _actor_matches(a, ref)), None)
        if match is None:
            missing.append(ref)
        else:
            resolved.append(match)
    return resolved, missing


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


def _load_asset(unreal_mod, path: str):
    loader = getattr(unreal_mod, "EditorAssetLibrary", None)
    if loader is None:
        return None
    try:
        return loader.load_asset(path)
    except Exception:
        return None


def _asset_exists(unreal_mod, path: str) -> bool:
    loader = getattr(unreal_mod, "EditorAssetLibrary", None)
    if loader is None:
        return False
    try:
        return bool(loader.does_asset_exist(path))
    except Exception:
        return False


def handle_duplicate_actors(command: Dict[str, Any]) -> Dict[str, Any]:
    """Duplicate a list of actors with an optional world-space offset."""

    actor_names = command.get("actor_names") or []
    offset = command.get("offset") or (0.0, 0.0, 0.0)

    if not actor_names:
        return err(error_code="MISSING_PARAMETERS", message="actor_names is required")

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("duplicate_actors requires a running Unreal Editor.")

    subsystem = _get_actor_subsystem(unreal_mod)
    if subsystem is None:
        return err(error_code="ACTOR_OPERATION_FAILED", message="EditorActorSubsystem is not available.")

    actors, missing = _find_actors(unreal_mod, actor_names)
    if missing:
        return err(
            error_code="ACTOR_NOT_FOUND",
            message=f"Could not resolve actors: {missing}",
            data={"missing_actors": missing},
        )

    offset_vec = _vector(unreal_mod, offset, (0.0, 0.0, 0.0))
    duplicated: List[Dict[str, Any]] = []
    warnings: List[str] = []

    # Prefer batch API if exposed.
    batch = getattr(subsystem, "duplicate_actors", None)
    try:
        dupes = []
        if callable(batch):
            dupes = list(batch(actors, None, offset_vec) or [])
        else:
            single = getattr(subsystem, "duplicate_actor", None)
            if not callable(single):
                return err(
                    error_code="ACTOR_OPERATION_FAILED",
                    message="Neither duplicate_actors nor duplicate_actor is available on EditorActorSubsystem.",
                )
            for actor in actors:
                try:
                    dupe = single(actor, None, offset_vec)
                except TypeError:
                    dupe = single(actor, offset_vec)
                if dupe is not None:
                    dupes.append(dupe)
    except Exception as exc:  # pragma: no cover
        return err(error_code="ACTOR_OPERATION_FAILED", message=str(exc))

    for dupe in dupes:
        label = ""
        try:
            label = str(dupe.get_actor_label())
        except Exception:
            pass
        duplicated.append({"label": label})

    if not duplicated:
        warnings.append("Engine returned no duplicated actors for the supplied input.")

    return ok(
        f"Duplicated {len(duplicated)} actor(s).",
        data={"duplicates": duplicated, "source_count": len(actors)},
        warnings=warnings,
    )


def _primary_mesh_component(unreal_mod, actor):
    """Return the best candidate static mesh component on ``actor``."""

    getter = getattr(actor, "get_components_by_class", None)
    smc_cls = getattr(unreal_mod, "StaticMeshComponent", None)
    if callable(getter) and smc_cls is not None:
        try:
            comps = list(getter(smc_cls) or [])
            if comps:
                return comps[0]
        except Exception:
            pass
    # StaticMeshActor has a direct accessor.
    sm_component = getattr(actor, "static_mesh_component", None)
    if sm_component is not None:
        return sm_component
    get_prop = getattr(actor, "get_editor_property", None)
    if callable(get_prop):
        try:
            return get_prop("static_mesh_component")
        except Exception:
            return None
    return None


def handle_replace_static_mesh(command: Dict[str, Any]) -> Dict[str, Any]:
    """Swap the static mesh on a set of actors (StaticMeshActor or equivalent)."""

    actor_names = command.get("actor_names") or []
    mesh_path = str(command.get("mesh_path") or "").strip()

    if not actor_names:
        return err(error_code="MISSING_PARAMETERS", message="actor_names is required")
    if not mesh_path:
        return err(error_code="MISSING_PARAMETERS", message="mesh_path is required")

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("replace_static_mesh requires a running Unreal Editor.")

    if not _asset_exists(unreal_mod, mesh_path):
        return err(error_code="MESH_NOT_FOUND", message=f"Mesh asset not found: {mesh_path}")

    mesh = _load_asset(unreal_mod, mesh_path)
    if mesh is None:
        return err(error_code="MESH_NOT_FOUND", message=f"Could not load mesh: {mesh_path}")

    sm_cls = getattr(unreal_mod, "StaticMesh", None)
    if sm_cls is not None:
        try:
            if not isinstance(mesh, sm_cls):
                return err(
                    error_code="MESH_NOT_FOUND",
                    message=f"Asset at {mesh_path} is not a StaticMesh (got {type(mesh).__name__}).",
                )
        except Exception:
            pass

    actors, missing = _find_actors(unreal_mod, actor_names)
    if missing:
        return err(
            error_code="ACTOR_NOT_FOUND",
            message=f"Could not resolve actors: {missing}",
            data={"missing_actors": missing},
        )

    updated: List[str] = []
    warnings: List[str] = []
    for actor in actors:
        component = _primary_mesh_component(unreal_mod, actor)
        if component is None:
            try:
                warnings.append(f"{actor.get_actor_label()}: no static mesh component")
            except Exception:
                warnings.append("Actor missing static mesh component")
            continue
        try:
            component.set_static_mesh(mesh)
            updated.append(str(actor.get_actor_label()))
        except Exception as exc:
            warnings.append(f"{actor.get_actor_label()}: {exc}")

    return ok(
        f"Replaced static mesh on {len(updated)} actor(s).",
        data={"updated_actors": updated, "mesh_path": mesh_path},
        warnings=warnings,
    )


def handle_replace_material(command: Dict[str, Any]) -> Dict[str, Any]:
    """Assign a material to the primary mesh of each actor at a given slot."""

    actor_names = command.get("actor_names") or []
    material_path = str(command.get("material_path") or "").strip()
    slot_index = int(command.get("slot_index", 0) or 0)

    if not actor_names:
        return err(error_code="MISSING_PARAMETERS", message="actor_names is required")
    if not material_path:
        return err(error_code="MISSING_PARAMETERS", message="material_path is required")

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("replace_material requires a running Unreal Editor.")

    if not _asset_exists(unreal_mod, material_path):
        return err(error_code="MATERIAL_NOT_FOUND", message=f"Material asset not found: {material_path}")

    material = _load_asset(unreal_mod, material_path)
    if material is None:
        return err(error_code="MATERIAL_NOT_FOUND", message=f"Could not load material: {material_path}")

    actors, missing = _find_actors(unreal_mod, actor_names)
    if missing:
        return err(
            error_code="ACTOR_NOT_FOUND",
            message=f"Could not resolve actors: {missing}",
            data={"missing_actors": missing},
        )

    updated: List[str] = []
    warnings: List[str] = []
    for actor in actors:
        component = _primary_mesh_component(unreal_mod, actor)
        if component is None:
            try:
                warnings.append(f"{actor.get_actor_label()}: no mesh component")
            except Exception:
                warnings.append("Actor missing mesh component")
            continue
        try:
            component.set_material(slot_index, material)
            updated.append(str(actor.get_actor_label()))
        except Exception as exc:
            warnings.append(f"{actor.get_actor_label()}: {exc}")

    return ok(
        f"Replaced material on {len(updated)} actor(s).",
        data={"updated_actors": updated, "material_path": material_path, "slot_index": slot_index},
        warnings=warnings,
    )


def handle_group_actors(command: Dict[str, Any]) -> Dict[str, Any]:
    """Group a set of actors under a World Outliner folder path."""

    actor_names = command.get("actor_names") or []
    group_name = str(command.get("group_name") or "").strip()

    if not actor_names:
        return err(error_code="MISSING_PARAMETERS", message="actor_names is required")
    if not group_name:
        return err(error_code="MISSING_PARAMETERS", message="group_name is required")

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("group_actors requires a running Unreal Editor.")

    actors, missing = _find_actors(unreal_mod, actor_names)
    if missing:
        return err(
            error_code="ACTOR_NOT_FOUND",
            message=f"Could not resolve actors: {missing}",
            data={"missing_actors": missing},
        )

    folder_path = group_name if group_name.startswith("/") else f"/{group_name}"

    grouped: List[str] = []
    warnings: List[str] = []
    for actor in actors:
        setter = getattr(actor, "set_folder_path", None)
        if callable(setter):
            try:
                setter(folder_path)
                grouped.append(str(actor.get_actor_label()))
                continue
            except Exception as exc:
                warnings.append(f"{actor.get_actor_label()}: {exc}")
                continue
        # Fallback via editor property
        try:
            actor.set_editor_property("folder_path", folder_path)
            grouped.append(str(actor.get_actor_label()))
        except Exception as exc:
            warnings.append(f"{actor.get_actor_label()}: {exc}")

    return ok(
        f"Grouped {len(grouped)} actor(s) under {folder_path}.",
        data={"grouped_actors": grouped, "folder_path": folder_path},
        warnings=warnings,
    )


def handle_select_actors(command: Dict[str, Any]) -> Dict[str, Any]:
    """Select actors in the World Outliner matching a name/label query."""

    query = str(command.get("query") or "").strip()
    match_mode = str(command.get("match", "contains")).strip().casefold() or "contains"

    if not query:
        return err(error_code="MISSING_PARAMETERS", message="query is required")

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("select_actors requires a running Unreal Editor.")

    subsystem = _get_actor_subsystem(unreal_mod)
    actors = _all_level_actors(unreal_mod)

    def _matches(actor) -> bool:
        fields = []
        for getter in ("get_actor_label", "get_name"):
            fn = getattr(actor, getter, None)
            if callable(fn):
                try:
                    fields.append(str(fn()))
                except Exception:
                    pass
        for field in fields:
            if match_mode == "exact" and field == query:
                return True
            if match_mode == "contains" and query.casefold() in field.casefold():
                return True
            if match_mode == "prefix" and field.casefold().startswith(query.casefold()):
                return True
        return False

    matched = [a for a in actors if _matches(a)]

    if subsystem is None or not hasattr(subsystem, "set_selected_level_actors"):
        return err(
            error_code="ACTOR_OPERATION_FAILED",
            message="EditorActorSubsystem.set_selected_level_actors is not available.",
            data={"matched_count": len(matched)},
        )

    try:
        subsystem.set_selected_level_actors(matched)
    except Exception as exc:
        return err(error_code="ACTOR_OPERATION_FAILED", message=str(exc))

    labels: List[str] = []
    for actor in matched:
        try:
            labels.append(str(actor.get_actor_label()))
        except Exception:
            continue

    return ok(
        f"Selected {len(labels)} actor(s) matching {query!r}.",
        data={"selected_actors": labels, "query": query, "match": match_mode},
    )
