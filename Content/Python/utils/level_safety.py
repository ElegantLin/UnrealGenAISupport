"""Guardrails and helpers for level / level-instance operations.

Pure-Python helpers so they can be unit-tested without the ``unreal`` module.

The combination ``add_level_to_world_with_transform(..., LevelStreamingLevelInstanceEditor, ...)``
caused a fatal ``Cast of nullptr to Actor failed`` crash on UE 5.4.4 during
the original post-mortem session.  Any raw attempt to invoke that path must
be refused by the MCP layer until it is explicitly validated on a target
engine build.  The refusal lives here so both ``level_commands`` handlers and
``execute_python`` classification share a single source of truth.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple


# The exact streaming class that produced the original crash.  Matching is
# case-insensitive and tolerant to the ``/Script/Engine.`` path prefix.
FORBIDDEN_STREAMING_CLASSES: Tuple[str, ...] = (
    "levelstreaminglevelinstanceeditor",
    "/script/engine.levelstreaminglevelinstanceeditor",
)


SUPPORTED_ADD_LEVEL_MODES: Tuple[str, ...] = (
    "sublevel",
    "level_instance",
    "packed_level",
)


# Canonical engine templates the ``create_level_from_template`` API
# accepts out of the box.  Callers may always pass an arbitrary
# ``/Engine/...`` or ``/Game/...`` asset path instead.
KNOWN_LEVEL_TEMPLATES = {
    "basic": "/Engine/Maps/Templates/Template_Default",
    "default": "/Engine/Maps/Templates/Template_Default",
    "empty": "/Engine/Maps/Templates/OpenWorld",
    "openworld": "/Engine/Maps/Templates/OpenWorld",
    "open_world": "/Engine/Maps/Templates/OpenWorld",
}


def _normalize(value) -> str:
    return str(value or "").strip().replace("-", "_").casefold()


def is_forbidden_streaming_class(class_name) -> bool:
    """Return True if ``class_name`` names the crash-triggering class."""

    text = _normalize(class_name)
    if not text:
        return False
    text = text.replace(" ", "")
    return text in FORBIDDEN_STREAMING_CLASSES or text.endswith(
        ".levelstreaminglevelinstanceeditor"
    )


def validate_add_level_mode(mode) -> str:
    """Return the canonical ``mode`` string or raise ``ValueError``."""

    text = _normalize(mode)
    if text in SUPPORTED_ADD_LEVEL_MODES:
        return text
    raise ValueError(
        f"Unsupported add_level_to_world mode '{mode}'. "
        f"Expected one of {SUPPORTED_ADD_LEVEL_MODES}."
    )


def resolve_template(template) -> str:
    """Resolve a user-supplied template name to a concrete asset path.

    Paths beginning with ``/Game`` or ``/Engine`` pass through unchanged
    (after ``strip()``).  Known shorthand names are remapped.  Anything else
    raises ``ValueError``.
    """

    text = str(template or "").strip()
    if not text:
        raise ValueError("template is required")
    if text.startswith("/"):
        return text
    key = _normalize(text)
    if key in KNOWN_LEVEL_TEMPLATES:
        return KNOWN_LEVEL_TEMPLATES[key]
    raise ValueError(
        f"Unknown level template '{template}'. "
        f"Pass an explicit /Engine or /Game asset path, or one of "
        f"{sorted(set(KNOWN_LEVEL_TEMPLATES))}."
    )


def normalize_actor_names(names: Iterable[str]) -> List[str]:
    """Return a deduplicated, whitespace-stripped list of actor names."""

    seen: set = set()
    result: List[str] = []
    for raw in names or ():
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def validate_level_asset_path(path) -> str:
    """Basic static validation for a ``/Game/...`` level asset path."""

    text = str(path or "").strip()
    if not text:
        raise ValueError("level_path is required")
    if not text.startswith("/"):
        raise ValueError(
            f"level_path must be an Unreal asset path starting with '/' (got {path!r})"
        )
    return text


def build_refusal_hint() -> str:
    """Human-readable hint returned when a forbidden class is requested."""

    return (
        "LevelStreamingLevelInstanceEditor crashed UE 5.4.4 when combined with "
        "add_level_to_world_with_transform. Use spawn_level_instance, "
        "create_level_instance_from_selection, or add_level_to_world with "
        "mode='sublevel' instead."
    )
