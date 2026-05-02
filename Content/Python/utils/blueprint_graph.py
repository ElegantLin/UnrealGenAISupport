"""Pure-Python helpers for Blueprint graph navigation and pin compatibility.

The corresponding Editor-side work happens in ``UGenBlueprintUtils`` on the
C++ side.  This module is intentionally Editor-free so the protocol contract
(graph path normalization, pin compatibility error formatting, autocast hints,
node selector parsing) can be exercised under pytest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# Categories that mirror Unreal's K2Schema ``PC_*`` strings.  Centralising
# them here lets us teach the suggester about cross-category coercions in a
# single place.
PIN_CATEGORIES = (
    "exec",
    "bool",
    "byte",
    "int",
    "int64",
    "real",        # In UE5, ``float`` and ``double`` share the ``real`` category
    "float",
    "double",
    "name",
    "string",
    "text",
    "object",
    "class",
    "interface",
    "struct",
    "enum",
    "delegate",
    "wildcard",
)


# Pairs of (source_category, target_category) that the schema can autocast.
# Each value is the conversion node hint we surface to the LLM.
AUTOCAST_HINTS: Dict[Tuple[str, str], str] = {
    ("int", "int64"): "Conv_IntToInt64",
    ("int64", "int"): "Conv_Int64ToInt",
    ("int", "real"): "Conv_IntToFloat",
    ("int", "float"): "Conv_IntToFloat",
    ("int", "double"): "Conv_IntToDouble",
    ("float", "double"): "Conv_FloatToDouble",
    ("double", "float"): "Conv_DoubleToFloat",
    ("real", "string"): "Conv_FloatToString",
    ("float", "string"): "Conv_FloatToString",
    ("int", "string"): "Conv_IntToString",
    ("bool", "string"): "Conv_BoolToString",
    ("name", "string"): "Conv_NameToString",
    ("text", "string"): "Conv_TextToString",
    ("string", "name"): "Conv_StringToName",
    ("string", "text"): "Conv_StringToText",
    ("byte", "int"): "Conv_ByteToInt",
    ("int", "byte"): "Conv_IntToByte",
    ("vector", "rotator"): "Conv_VectorToRotator",
    ("rotator", "vector"): "Conv_RotatorToVector",
}


_REAL_LIKE = {"float", "double", "real"}


def _norm_category(value: Any) -> str:
    return str(value or "").strip().casefold()


def _norm_subcategory(value: Any) -> str:
    return str(value or "").strip()


def normalize_graph_path(path: Any) -> str:
    """Collapse separators and strip leading/trailing slashes from a graph path.

    A graph path looks like ``EventGraph/MyFunction/InnerNode`` or
    ``UbergraphPages/EventGraph``.  We accept ``\\``, ``.`` or ``::`` as
    separators and normalise to ``/``.  Unreal's ``UEdGraph::GetPathName`` can
    also return full object paths such as ``/Game/BP.BP:EventGraph``; callers
    should be able to pass those schema paths back unchanged.
    """

    raw = str(path or "").strip().replace("\\", "/").replace("::", "/")
    if ":" in raw:
        prefix, _, suffix = raw.rpartition(":")
        if prefix.startswith("/") or "/" in prefix or "." in prefix:
            raw = suffix
    cleaned = raw.replace(".", "/")
    parts = [segment.strip() for segment in cleaned.split("/")]
    parts = [segment for segment in parts if segment]
    return "/".join(parts)


def split_graph_path(path: Any) -> List[str]:
    normalized = normalize_graph_path(path)
    if not normalized:
        return []
    return normalized.split("/")


def parse_node_selector(selector: Any) -> Dict[str, str]:
    """Parse a node selector string into structured fields.

    Supported syntaxes:
        ``"<graph_path>:<identifier>"``
        ``"<identifier>"`` (graph defaults to empty / current)

    The identifier may be a node GUID, a node name, or a stable selector such
    as ``EventBeginPlay``.  We do *not* try to disambiguate here; the C++
    side resolves the actual node.  This helper exists so handlers can fail
    fast on malformed input and produce consistent error envelopes.
    """

    text = str(selector or "").strip()
    if not text:
        return {"graph_path": "", "identifier": "", "kind": "unknown"}

    if ":" in text:
        graph_part, _, identifier_part = text.partition(":")
        graph_path = normalize_graph_path(graph_part)
        identifier = identifier_part.strip()
    else:
        graph_path = ""
        identifier = text

    kind = _classify_identifier(identifier)
    return {"graph_path": graph_path, "identifier": identifier, "kind": kind}


def _classify_identifier(identifier: str) -> str:
    if not identifier:
        return "unknown"
    stripped = identifier.replace("-", "")
    if len(stripped) == 32 and all(ch in "0123456789abcdefABCDEF" for ch in stripped):
        return "guid"
    if identifier.startswith("Event") or identifier.startswith("K2Node_"):
        return "event"
    return "name"


@dataclass(frozen=True)
class PinDescriptor:
    name: str
    direction: str          # "input" or "output"
    category: str
    sub_category: str = ""
    container_type: str = "none"   # "none" | "array" | "set" | "map"
    is_reference: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "direction": self.direction,
            "category": self.category,
            "sub_category": self.sub_category,
            "container_type": self.container_type,
            "is_reference": self.is_reference,
        }


def _are_real_like(a: str, b: str) -> bool:
    return a in _REAL_LIKE and b in _REAL_LIKE


def evaluate_pin_compatibility(source: PinDescriptor, target: PinDescriptor) -> Dict[str, Any]:
    """Return a structured ``{compatible, reason, autocast_suggestion}`` dict.

    The result mirrors what the C++ side reports to keep error envelopes
    aligned across both transports.
    """

    if source.direction == target.direction:
        return _incompatible(
            f"Both pins are {source.direction}s; one must be an input and the other an output.",
            "PIN_DIRECTION_MISMATCH",
        )

    if source.container_type != target.container_type:
        return _incompatible(
            (
                f"Container mismatch: source is {source.container_type or 'none'} "
                f"but target is {target.container_type or 'none'}."
            ),
            "PIN_CONTAINER_MISMATCH",
        )

    src_cat = _norm_category(source.category)
    tgt_cat = _norm_category(target.category)

    if src_cat == "exec" or tgt_cat == "exec":
        if src_cat == tgt_cat:
            return _compatible()
        return _incompatible(
            "Exec pins can only connect to other exec pins.",
            "PIN_EXEC_MISMATCH",
        )

    if "wildcard" in (src_cat, tgt_cat):
        return _compatible()

    if src_cat == tgt_cat:
        if src_cat in {"object", "class", "interface", "struct", "enum"}:
            if source.sub_category and target.sub_category and source.sub_category != target.sub_category:
                return _incompatible(
                    (
                        f"Same category ({src_cat}) but incompatible sub-types: "
                        f"source={source.sub_category}, target={target.sub_category}."
                    ),
                    "PIN_SUBTYPE_MISMATCH",
                )
        return _compatible()

    if _are_real_like(src_cat, tgt_cat):
        return _compatible()

    suggestion = AUTOCAST_HINTS.get((src_cat, tgt_cat))
    if suggestion:
        return _incompatible(
            (
                f"Direct connection from {src_cat} to {tgt_cat} is not supported; "
                f"insert a '{suggestion}' conversion node."
            ),
            "PIN_AUTOCAST_REQUIRED",
            autocast_suggestion=suggestion,
        )

    return _incompatible(
        f"Pin categories are incompatible: source={src_cat or 'unknown'}, target={tgt_cat or 'unknown'}.",
        "PIN_INCOMPATIBLE",
    )


def suggest_autocast(source_category: Any, target_category: Any) -> Optional[str]:
    src = _norm_category(source_category)
    tgt = _norm_category(target_category)
    return AUTOCAST_HINTS.get((src, tgt))


def _compatible() -> Dict[str, Any]:
    return {
        "compatible": True,
        "reason": "Pins are directly compatible.",
        "error_code": None,
        "autocast_suggestion": None,
    }


def _incompatible(reason: str, error_code: str, *, autocast_suggestion: Optional[str] = None) -> Dict[str, Any]:
    return {
        "compatible": False,
        "reason": reason,
        "error_code": error_code,
        "autocast_suggestion": autocast_suggestion,
    }


def format_connection_diagnostics(
    *,
    source_node: str,
    source_pin: str,
    target_node: str,
    target_pin: str,
    compatibility: Dict[str, Any],
) -> Dict[str, Any]:
    """Compose the structured payload returned by ``connect_nodes``."""

    return {
        "source_node": source_node,
        "source_pin": source_pin,
        "target_node": target_node,
        "target_pin": target_pin,
        "compatible": bool(compatibility.get("compatible")),
        "reason": compatibility.get("reason", ""),
        "error_code": compatibility.get("error_code"),
        "autocast_suggestion": compatibility.get("autocast_suggestion"),
    }


def collect_supported_graph_types() -> List[str]:
    return [
        "UbergraphPages",
        "FunctionGraphs",
        "MacroGraphs",
        "DelegateSignatureGraphs",
        "AnimationGraphs",
        "AnimationStateMachineGraphs",
        "WidgetGraphs",
        "SubGraphs",
    ]


def normalize_pin_descriptor(payload: Any) -> Optional[PinDescriptor]:
    """Coerce a free-form dict into a :class:`PinDescriptor`."""

    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name", "")).strip()
    if not name:
        return None
    direction = str(payload.get("direction", "")).strip().casefold()
    if direction not in {"input", "output"}:
        return None
    return PinDescriptor(
        name=name,
        direction=direction,
        category=str(payload.get("category", "")).strip().casefold(),
        sub_category=str(payload.get("sub_category", "") or payload.get("subCategory", "") or "").strip(),
        container_type=str(payload.get("container_type", "none")).strip().casefold() or "none",
        is_reference=bool(payload.get("is_reference", False)),
    )


def normalize_pin_pair(
    source: Any,
    target: Any,
) -> Tuple[Optional[PinDescriptor], Optional[PinDescriptor]]:
    return normalize_pin_descriptor(source), normalize_pin_descriptor(target)


def filter_graphs_by_kind(graphs: Iterable[Dict[str, Any]], kind: Optional[str]) -> List[Dict[str, Any]]:
    if not kind:
        return list(graphs or [])
    target = str(kind).strip().casefold()
    return [g for g in (graphs or []) if str(g.get("kind", "")).strip().casefold() == target]


def stable_node_signature(node: Dict[str, Any]) -> str:
    """Build a deterministic short selector usable to find a node again.

    Order of preference: ``guid`` -> ``stable_name`` -> ``name`` + ``class``.
    """

    if not isinstance(node, dict):
        return ""
    guid = str(node.get("guid") or "").strip()
    if guid:
        return guid
    stable = str(node.get("stable_name") or "").strip()
    if stable:
        return stable
    name = str(node.get("name") or "").strip()
    klass = str(node.get("class") or "").strip()
    if name and klass:
        return f"{klass}::{name}"
    return name or klass


def merge_compile_diagnostics(payloads: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge multiple per-graph diagnostic payloads into a single envelope."""

    warnings: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    success = True
    for payload in payloads or []:
        if not isinstance(payload, dict):
            continue
        if not payload.get("success", True):
            success = False
        for warning in payload.get("warnings", []) or []:
            warnings.append(dict(warning) if isinstance(warning, dict) else {"message": str(warning)})
        for error in payload.get("errors", []) or []:
            errors.append(dict(error) if isinstance(error, dict) else {"message": str(error)})
    return {
        "success": success and not errors,
        "warnings": warnings,
        "errors": errors,
    }
