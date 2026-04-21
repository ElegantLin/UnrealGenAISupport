"""Pure-Python safety helpers shared by ``execute_python`` and friends.

The legacy implementation embedded a few hard-coded substring checks inside the
handler.  Pulling that out into a dedicated module keeps the rules unit
testable, makes their evolution visible in code review, and lets the MCP layer
expose ``unsafe_commands`` consistently in capability negotiation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set


# Hard list of unsafe MCP command names exposed via ``get_capabilities``.  The
# values are the ``type`` field used over the socket transport.
UNSAFE_COMMANDS: tuple = (
    "execute_python",
    "execute_unreal_command",
    "request_editor_restart",
    "raw_anim_blueprint_node_edit",
)


# Substrings that suggest a script will mutate or destroy assets/files.
DESTRUCTIVE_SUBSTRINGS: tuple = (
    "unreal.editorassetlibrary.delete_asset",
    "unreal.editorassetlibrary.save_asset",
    "unreal.editorassetlibrary.duplicate_asset",
    "unreal.editorassetlibrary.rename_asset",
    "unreal.editorlevellibrary.destroy_actor",
    "unreal.editorlevellibrary.save_current_level",
    "unreal.save_package",
    "save_packages_with_dialog",
    "savepackage(",
    "os.remove",
    "shutil.rmtree",
    "shutil.move",
    "open(",
    "pathlib.path.unlink",
    # Crash-triggering level-instance editor combo (postmortem UE 5.4.4).
    "levelstreaminglevelinstanceeditor",
    "add_level_to_world_with_transform",
)


# Substrings that, on their own, almost certainly write to disk somewhere
# even if a simple keyword scan can't be 100% sure.
SIDE_EFFECT_SUBSTRINGS: tuple = (
    ".save_package",
    ".save_loaded_asset",
    ".save_current_level",
    ".compile_blueprint",
    ".write(",
    ".unlink(",
    ".rmtree(",
    "subprocess.",
    "shutil.",
)


@dataclass(frozen=True)
class ScriptClassification:
    """Result of classifying a Python script before executing it."""

    classification: str  # "read_only", "side_effect", "destructive"
    triggers: List[str]
    is_destructive: bool
    requires_force: bool

    def to_dict(self) -> dict:
        return {
            "classification": self.classification,
            "triggers": list(self.triggers),
            "is_destructive": self.is_destructive,
            "requires_force": self.requires_force,
        }


def _matches(script_lower: str, candidates: Iterable[str]) -> List[str]:
    matched: List[str] = []
    for needle in candidates:
        if needle and needle in script_lower and needle not in matched:
            matched.append(needle)
    return matched


def classify_script(
    script: str,
    *,
    extra_destructive: Optional[Sequence[str]] = None,
) -> ScriptClassification:
    """Classify a Python script and return a structured verdict.

    ``read_only`` is a best-effort guess; callers must still treat any
    ``execute_python`` invocation as ``unsafe`` from a capabilities standpoint.
    """

    text = (script or "").casefold()
    destructive_pool = list(DESTRUCTIVE_SUBSTRINGS) + list(extra_destructive or [])
    destructive_hits = _matches(text, destructive_pool)
    side_effect_hits = _matches(text, SIDE_EFFECT_SUBSTRINGS)

    if destructive_hits:
        return ScriptClassification(
            classification="destructive",
            triggers=destructive_hits,
            is_destructive=True,
            requires_force=True,
        )
    if side_effect_hits:
        return ScriptClassification(
            classification="side_effect",
            triggers=side_effect_hits,
            is_destructive=False,
            requires_force=False,
        )
    return ScriptClassification(
        classification="read_only",
        triggers=[],
        is_destructive=False,
        requires_force=False,
    )


def summarize_dirty_packages(packages: Iterable[object]) -> List[str]:
    """Return a stable, deduplicated, human-readable list of dirty packages."""

    summary: List[str] = []
    seen: Set[str] = set()
    for pkg in packages or []:
        if pkg is None:
            continue
        # Accept either a UPackage-like object exposing get_name(), or an
        # already-stringified name.
        name = ""
        getter = getattr(pkg, "get_name", None)
        if callable(getter):
            try:
                name = str(getter() or "")
            except Exception:  # pragma: no cover - defensive path
                name = ""
        if not name:
            name = str(pkg)
        name = name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        summary.append(name)
    return summary


def is_unsafe_command(command_type: str) -> bool:
    if not command_type:
        return False
    return str(command_type).strip().casefold() in {c.casefold() for c in UNSAFE_COMMANDS}
