"""Pure helpers for the Enhanced Input slice (P2).

These normalize the user-facing vocabulary (``"E"`` / ``"LeftMouseButton"`` /
``"Gamepad_FaceButton_Bottom"``) and the set of triggers / modifiers that
``UEnhancedInput`` understands.  The handler calls into
``UGenEnhancedInputUtils`` with already-normalized arguments so the C++ side
stays minimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

VALUE_TYPES: Tuple[str, ...] = ("Digital", "Axis1D", "Axis2D", "Axis3D")

SUPPORTED_TRIGGERS: Tuple[str, ...] = (
    "Pressed",
    "Released",
    "Down",
    "Hold",
    "HoldAndRelease",
    "Tap",
    "Pulse",
    "ChordAction",
)

SUPPORTED_MODIFIERS: Tuple[str, ...] = (
    "Negate",
    "DeadZone",
    "Scalar",
    "Smooth",
    "SwizzleAxis",
    "FOVScaling",
)

# Common aliases -> canonical FKey name used by Unreal.
_KEY_ALIASES: Dict[str, str] = {
    "space": "SpaceBar",
    "spacebar": "SpaceBar",
    "lmb": "LeftMouseButton",
    "rmb": "RightMouseButton",
    "mmb": "MiddleMouseButton",
    "esc": "Escape",
    "return": "Enter",
    "ctrl": "LeftControl",
    "shift": "LeftShift",
    "alt": "LeftAlt",
    "left": "Left",
    "right": "Right",
    "up": "Up",
    "down": "Down",
    "mousewheelup": "MouseScrollUp",
    "mousewheeldown": "MouseScrollDown",
    "gamepad_a": "Gamepad_FaceButton_Bottom",
    "gamepad_b": "Gamepad_FaceButton_Right",
    "gamepad_x": "Gamepad_FaceButton_Left",
    "gamepad_y": "Gamepad_FaceButton_Top",
}


class InputMappingError(ValueError):
    """Raised when the caller supplied data the Enhanced Input layer cannot map."""


@dataclass
class KeyBinding:
    """A single ``(action, key)`` pair inside a mapping context."""

    action_path: str
    key: str
    triggers: Tuple[str, ...] = ()
    modifiers: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "action_path": self.action_path,
            "key": self.key,
            "triggers": list(self.triggers),
            "modifiers": list(self.modifiers),
        }


def normalize_key_name(raw: str) -> str:
    """Return the canonical ``FKey`` name for a user-supplied key token."""

    if raw is None:
        raise InputMappingError("key is required")
    text = str(raw).strip()
    if not text:
        raise InputMappingError("key is required")

    lower = text.lower()
    if lower in _KEY_ALIASES:
        return _KEY_ALIASES[lower]

    # Single-letter keys become uppercase ("e" -> "E").
    if len(text) == 1 and text.isalpha():
        return text.upper()

    # Digit keys get the ``Zero``..``Nine`` canonical name used by FKey.
    if len(text) == 1 and text.isdigit():
        digit_map = {
            "0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four",
            "5": "Five", "6": "Six", "7": "Seven", "8": "Eight", "9": "Nine",
        }
        return digit_map[text]

    # Leave everything else as-is; C++ layer will reject invalid FKey names.
    return text


def normalize_trigger(trigger: str) -> str:
    if not trigger:
        raise InputMappingError("trigger is required")
    for candidate in SUPPORTED_TRIGGERS:
        if candidate.lower() == str(trigger).strip().lower():
            return candidate
    raise InputMappingError(f"unsupported trigger: {trigger!r}")


def normalize_modifier(modifier: str) -> str:
    if not modifier:
        raise InputMappingError("modifier is required")
    for candidate in SUPPORTED_MODIFIERS:
        if candidate.lower() == str(modifier).strip().lower():
            return candidate
    raise InputMappingError(f"unsupported modifier: {modifier!r}")


def normalize_value_type(value_type: str) -> str:
    if not value_type:
        return "Digital"
    for candidate in VALUE_TYPES:
        if candidate.lower() == str(value_type).strip().lower():
            return candidate
    raise InputMappingError(f"unsupported value_type: {value_type!r}")


def build_binding(
    action_path: str,
    key: str,
    triggers: Optional[Iterable[str]] = None,
    modifiers: Optional[Iterable[str]] = None,
) -> KeyBinding:
    action_path = (action_path or "").strip()
    if not action_path:
        raise InputMappingError("action_path is required")
    return KeyBinding(
        action_path=action_path,
        key=normalize_key_name(key),
        triggers=tuple(normalize_trigger(t) for t in (triggers or ())),
        modifiers=tuple(normalize_modifier(m) for m in (modifiers or ())),
    )


def diff_bindings(
    current: Iterable[KeyBinding],
    desired: Iterable[KeyBinding],
) -> Dict[str, List[KeyBinding]]:
    """Return ``{'added': [...], 'removed': [...], 'unchanged': [...]}``."""

    current_set = {(b.action_path, b.key, b.triggers, b.modifiers) for b in current}
    desired_list = list(desired)
    desired_set = {(b.action_path, b.key, b.triggers, b.modifiers) for b in desired_list}

    added = [b for b in desired_list if (b.action_path, b.key, b.triggers, b.modifiers) not in current_set]
    removed_keys = current_set - desired_set
    removed = [
        KeyBinding(action_path=ap, key=k, triggers=tr, modifiers=md)
        for (ap, k, tr, md) in removed_keys
    ]
    unchanged = [b for b in desired_list if (b.action_path, b.key, b.triggers, b.modifiers) in current_set]
    return {"added": added, "removed": removed, "unchanged": unchanged}


def legacy_binding_warning(project_uses_enhanced_input: bool) -> Optional[Dict[str, str]]:
    """Return a structured warning dict when the project already migrated to EI."""

    if not project_uses_enhanced_input:
        return None
    return {
        "error_code": "LEGACY_INPUT_PATH",
        "message": (
            "Project is configured for Enhanced Input; prefer map_enhanced_input_action "
            "and create_input_action instead of the legacy add_input_binding path."
        ),
    }
