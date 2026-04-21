"""Pure-Python helpers for AnimBlueprint structures (P4 + P5).

These helpers normalize request arguments, validate semantic edits, and
produce deterministic structured views of AnimBlueprint graphs so the C++
layer can be kept thin. Nothing here imports ``unreal``; all functions
operate on JSON-friendly dicts / dataclasses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCHEMA_VERSION = 1

# Graph roles the MCP understands. Anything else is surfaced verbatim but
# considered unsupported for semantic writes.
SUPPORTED_GRAPH_KINDS = (
    "AnimGraph",
    "StateMachine",
    "State",
    "Transition",
    "Alias",
    "EventGraph",
    "FunctionGraph",
)

SEMANTIC_NODE_KINDS = (
    "StateMachine",
    "State",
    "Transition",
    "StateAlias",
    "SequencePlayer",
    "BlendSpacePlayer",
    "CachedPose",
    "UseCachedPose",
    "DefaultSlot",
    "ApplyAdditive",
)

# Blend mode strings accepted by `set_state_blend_space_asset` etc.
SUPPORTED_ANIM_PLAY_MODES = ("Loop", "Once", "Freeze")


class AnimBlueprintError(ValueError):
    """Raised for AnimBlueprint argument validation failures."""


# ---------------------------------------------------------------------------
# Graph path parsing ---------------------------------------------------------
# ---------------------------------------------------------------------------

_PATH_SPLIT_RE = re.compile(r"/+")


def parse_graph_path(path: str) -> List[str]:
    """Split a graph path like ``AnimGraph/Locomotion/Walk`` into tokens.

    * Leading/trailing slashes are tolerated.
    * Empty path is rejected.
    * Tokens preserve their case.
    """
    if not isinstance(path, str):
        raise AnimBlueprintError("graph_path must be a string")
    cleaned = path.strip().strip("/")
    if not cleaned:
        raise AnimBlueprintError("graph_path is empty")
    parts = [p for p in _PATH_SPLIT_RE.split(cleaned) if p]
    if not parts:
        raise AnimBlueprintError("graph_path is empty")
    return parts


def join_graph_path(parts: Iterable[str]) -> str:
    """Join tokens produced by :func:`parse_graph_path` back into a path."""
    return "/".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Selectors ------------------------------------------------------------------
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StateMachineSelector:
    anim_blueprint_path: str
    state_machine: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "anim_blueprint_path": self.anim_blueprint_path,
            "state_machine": self.state_machine,
        }


@dataclass(frozen=True)
class StateSelector:
    anim_blueprint_path: str
    state_machine: str
    state: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "anim_blueprint_path": self.anim_blueprint_path,
            "state_machine": self.state_machine,
            "state": self.state,
        }


@dataclass(frozen=True)
class TransitionSelector:
    anim_blueprint_path: str
    state_machine: str
    from_state: str
    to_state: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "anim_blueprint_path": self.anim_blueprint_path,
            "state_machine": self.state_machine,
            "from_state": self.from_state,
            "to_state": self.to_state,
        }


def _require_non_empty(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AnimBlueprintError(f"{field_name} is required")
    return text


def selector_state_machine(data: Dict[str, Any]) -> StateMachineSelector:
    return StateMachineSelector(
        anim_blueprint_path=_require_non_empty(data.get("anim_blueprint_path"), "anim_blueprint_path"),
        state_machine=_require_non_empty(data.get("state_machine") or data.get("state_machine_name"), "state_machine"),
    )


def selector_state(data: Dict[str, Any]) -> StateSelector:
    base = selector_state_machine(data)
    return StateSelector(
        anim_blueprint_path=base.anim_blueprint_path,
        state_machine=base.state_machine,
        state=_require_non_empty(data.get("state") or data.get("state_name"), "state"),
    )


def selector_transition(data: Dict[str, Any]) -> TransitionSelector:
    base = selector_state_machine(data)
    return TransitionSelector(
        anim_blueprint_path=base.anim_blueprint_path,
        state_machine=base.state_machine,
        from_state=_require_non_empty(data.get("from_state") or data.get("from"), "from_state"),
        to_state=_require_non_empty(data.get("to_state") or data.get("to"), "to_state"),
    )


# ---------------------------------------------------------------------------
# Transition rule validation -------------------------------------------------
# ---------------------------------------------------------------------------

_RULE_KINDS = ("always", "bool_property", "expression")


@dataclass
class TransitionRule:
    kind: str
    expression: Optional[str] = None
    property_name: Optional[str] = None
    invert: bool = False
    blend_time: float = 0.2

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "expression": self.expression,
            "property_name": self.property_name,
            "invert": self.invert,
            "blend_time": self.blend_time,
        }


def normalize_transition_rule(data: Optional[Dict[str, Any]]) -> TransitionRule:
    payload = data or {}
    kind = str(payload.get("kind") or "").strip().lower()
    if not kind:
        kind = "always"
    if kind not in _RULE_KINDS:
        raise AnimBlueprintError(f"unknown transition rule kind: {kind}; expected one of {_RULE_KINDS}")

    blend_time_raw = payload.get("blend_time", 0.2)
    try:
        blend_time = float(blend_time_raw)
    except (TypeError, ValueError) as exc:
        raise AnimBlueprintError(f"blend_time must be numeric (got {blend_time_raw!r})") from exc
    if blend_time < 0.0:
        raise AnimBlueprintError("blend_time must be >= 0")

    invert = bool(payload.get("invert", False))

    if kind == "always":
        return TransitionRule(kind="always", blend_time=blend_time, invert=invert)

    if kind == "bool_property":
        prop = _require_non_empty(payload.get("property_name") or payload.get("property"), "property_name")
        return TransitionRule(kind=kind, property_name=prop, invert=invert, blend_time=blend_time)

    # expression
    expression = _require_non_empty(payload.get("expression"), "expression")
    # Very defensive sanity check: reject obviously unsafe characters so we
    # fail fast rather than letting the editor compile an invalid graph.
    if any(ch in expression for ch in (";", "\n", "\r")):
        raise AnimBlueprintError("expression must be a single line without ';'")
    return TransitionRule(kind=kind, expression=expression, invert=invert, blend_time=blend_time)


# ---------------------------------------------------------------------------
# State content payloads -----------------------------------------------------
# ---------------------------------------------------------------------------


def normalize_play_mode(value: Any, default: str = "Loop") -> str:
    text = str(value or default).strip()
    if not text:
        text = default
    for supported in SUPPORTED_ANIM_PLAY_MODES:
        if text.lower() == supported.lower():
            return supported
    raise AnimBlueprintError(
        f"unknown play_mode '{text}'; expected one of {SUPPORTED_ANIM_PLAY_MODES}"
    )


@dataclass
class StateAssetBinding:
    asset_path: str
    play_rate: float = 1.0
    play_mode: str = "Loop"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_path": self.asset_path,
            "play_rate": self.play_rate,
            "play_mode": self.play_mode,
        }


def normalize_state_asset_binding(data: Dict[str, Any]) -> StateAssetBinding:
    asset_path = _require_non_empty(data.get("asset_path") or data.get("animation_path"), "asset_path")
    try:
        play_rate = float(data.get("play_rate", 1.0))
    except (TypeError, ValueError) as exc:
        raise AnimBlueprintError("play_rate must be numeric") from exc
    if play_rate <= 0.0:
        raise AnimBlueprintError("play_rate must be > 0")
    play_mode = normalize_play_mode(data.get("play_mode"))
    return StateAssetBinding(asset_path=asset_path, play_rate=play_rate, play_mode=play_mode)


# ---------------------------------------------------------------------------
# State machine structure summaries -----------------------------------------
# ---------------------------------------------------------------------------


@dataclass
class PinSummary:
    name: str
    direction: str
    pin_type: str
    default_value: Optional[str] = None
    linked_to: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "direction": self.direction,
            "pin_type": self.pin_type,
            "default_value": self.default_value,
            "linked_to": list(self.linked_to),
        }


@dataclass
class NodeSummary:
    node_id: str
    kind: str
    title: str
    graph_path: str
    pins: List[PinSummary] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "title": self.title,
            "graph_path": self.graph_path,
            "pins": [p.to_dict() for p in self.pins],
            "properties": dict(self.properties),
        }


def normalize_pin(data: Dict[str, Any]) -> PinSummary:
    return PinSummary(
        name=str(data.get("name") or ""),
        direction=str(data.get("direction") or data.get("dir") or "input").lower(),
        pin_type=str(data.get("pin_type") or data.get("type") or "wildcard"),
        default_value=None if data.get("default_value") in (None, "") else str(data.get("default_value")),
        linked_to=[str(x) for x in (data.get("linked_to") or [])],
    )


def normalize_node(data: Dict[str, Any]) -> NodeSummary:
    pins = [normalize_pin(p) for p in (data.get("pins") or [])]
    return NodeSummary(
        node_id=str(data.get("node_id") or data.get("guid") or ""),
        kind=str(data.get("kind") or data.get("type") or ""),
        title=str(data.get("title") or data.get("name") or ""),
        graph_path=str(data.get("graph_path") or ""),
        pins=pins,
        properties=dict(data.get("properties") or {}),
    )


@dataclass
class TransitionSummary:
    from_state: str
    to_state: str
    rule: Dict[str, Any] = field(default_factory=lambda: {"kind": "always"})
    blend_time: float = 0.2

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_state": self.from_state,
            "to_state": self.to_state,
            "rule": dict(self.rule),
            "blend_time": self.blend_time,
        }


@dataclass
class StateSummary:
    name: str
    kind: str = "State"
    animation_asset: Optional[str] = None
    blend_space_asset: Optional[str] = None
    aliased_states: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "animation_asset": self.animation_asset,
            "blend_space_asset": self.blend_space_asset,
            "aliased_states": list(self.aliased_states),
        }


@dataclass
class StateMachineSummary:
    name: str
    entry_state: Optional[str] = None
    states: List[StateSummary] = field(default_factory=list)
    transitions: List[TransitionSummary] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "entry_state": self.entry_state,
            "states": [s.to_dict() for s in self.states],
            "transitions": [t.to_dict() for t in self.transitions],
        }


@dataclass
class AnimBlueprintStructure:
    anim_blueprint_path: str
    parent_class: str = ""
    target_skeleton: str = ""
    state_machines: List[StateMachineSummary] = field(default_factory=list)
    cached_poses: List[str] = field(default_factory=list)
    slots: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "anim_blueprint_path": self.anim_blueprint_path,
            "parent_class": self.parent_class,
            "target_skeleton": self.target_skeleton,
            "state_machines": [sm.to_dict() for sm in self.state_machines],
            "cached_poses": list(self.cached_poses),
            "slots": list(self.slots),
            "warnings": list(self.warnings),
        }


def _parse_transition(data: Dict[str, Any]) -> TransitionSummary:
    rule = data.get("rule") or {"kind": "always"}
    if not isinstance(rule, dict):
        rule = {"kind": "always"}
    try:
        blend_time = float(data.get("blend_time", 0.2))
    except (TypeError, ValueError):
        blend_time = 0.2
    return TransitionSummary(
        from_state=str(data.get("from_state") or data.get("from") or ""),
        to_state=str(data.get("to_state") or data.get("to") or ""),
        rule=rule,
        blend_time=blend_time,
    )


def _parse_state(data: Dict[str, Any]) -> StateSummary:
    return StateSummary(
        name=str(data.get("name") or ""),
        kind=str(data.get("kind") or "State"),
        animation_asset=(str(data["animation_asset"]) if data.get("animation_asset") else None),
        blend_space_asset=(str(data["blend_space_asset"]) if data.get("blend_space_asset") else None),
        aliased_states=[str(x) for x in (data.get("aliased_states") or [])],
    )


def parse_structure(payload: Dict[str, Any]) -> AnimBlueprintStructure:
    """Parse a JSON payload emitted by ``GenAnimationBlueprintUtils``."""
    state_machines: List[StateMachineSummary] = []
    for sm in payload.get("state_machines") or []:
        states = [_parse_state(s) for s in (sm.get("states") or [])]
        transitions = [_parse_transition(t) for t in (sm.get("transitions") or [])]
        state_machines.append(StateMachineSummary(
            name=str(sm.get("name") or ""),
            entry_state=(str(sm["entry_state"]) if sm.get("entry_state") else None),
            states=states,
            transitions=transitions,
        ))

    return AnimBlueprintStructure(
        anim_blueprint_path=str(payload.get("anim_blueprint_path") or ""),
        parent_class=str(payload.get("parent_class") or ""),
        target_skeleton=str(payload.get("target_skeleton") or ""),
        state_machines=state_machines,
        cached_poses=[str(x) for x in (payload.get("cached_poses") or [])],
        slots=[str(x) for x in (payload.get("slots") or [])],
        warnings=[str(x) for x in (payload.get("warnings") or [])],
    )


# ---------------------------------------------------------------------------
# Diff helper for verification reports --------------------------------------
# ---------------------------------------------------------------------------


def diff_structures(
    before: AnimBlueprintStructure,
    after: AnimBlueprintStructure,
) -> Dict[str, Any]:
    """Produce a tiny diff summary comparing two structure snapshots."""

    def _sm_keys(s: AnimBlueprintStructure) -> List[str]:
        return [sm.name for sm in s.state_machines]

    def _state_keys(s: AnimBlueprintStructure) -> List[Tuple[str, str]]:
        keys: List[Tuple[str, str]] = []
        for sm in s.state_machines:
            for state in sm.states:
                keys.append((sm.name, state.name))
        return keys

    def _transition_keys(s: AnimBlueprintStructure) -> List[Tuple[str, str, str]]:
        keys: List[Tuple[str, str, str]] = []
        for sm in s.state_machines:
            for t in sm.transitions:
                keys.append((sm.name, t.from_state, t.to_state))
        return keys

    sm_before = set(_sm_keys(before))
    sm_after = set(_sm_keys(after))
    state_before = set(_state_keys(before))
    state_after = set(_state_keys(after))
    trans_before = set(_transition_keys(before))
    trans_after = set(_transition_keys(after))

    return {
        "state_machines_added": sorted(sm_after - sm_before),
        "state_machines_removed": sorted(sm_before - sm_after),
        "states_added": sorted([f"{a}:{b}" for a, b in (state_after - state_before)]),
        "states_removed": sorted([f"{a}:{b}" for a, b in (state_before - state_after)]),
        "transitions_added": sorted([f"{a}:{b}->{c}" for a, b, c in (trans_after - trans_before)]),
        "transitions_removed": sorted([f"{a}:{b}->{c}" for a, b, c in (trans_before - trans_after)]),
    }
