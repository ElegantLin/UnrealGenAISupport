"""Pure-Python helpers for the editor session snapshot / restore flow (P1.5).

The session payload is a plain dict so it can be serialized to
``Saved/MCP/LastEditorSession.json`` by the C++ ``GenEditorSessionUtils`` or
by handler code running under pytest.  Keeping the shape logic here (instead
of in the handler) lets us regression test the normalization / restore policy
without an Unreal Editor.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1

RESTORE_POLICIES = ("none", "assets_only", "assets_and_tabs")
DEFAULT_RESTORE_POLICY = "assets_only"

# Asset classes that the first-stage restore actually supports.  Anything
# outside this set is skipped but reported as ``skipped`` so the caller can
# surface it without blocking the rest of the restore.
SUPPORTED_ASSET_CLASSES: Tuple[str, ...] = (
    "Blueprint",
    "WidgetBlueprint",
    "AnimBlueprint",
    "AnimSequence",
    "BlendSpace",
    "BlendSpace1D",
    "Material",
    "MaterialInstance",
)


@dataclass
class AssetEntry:
    """One entry in the captured ``open_asset_paths`` list."""

    asset_path: str
    asset_class: str = ""
    is_primary: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_path": self.asset_path,
            "asset_class": self.asset_class,
            "is_primary": bool(self.is_primary),
        }


@dataclass
class SessionSnapshot:
    """Normalized shape of ``Saved/MCP/LastEditorSession.json``."""

    schema_version: int = SCHEMA_VERSION
    captured_at: float = field(default_factory=time.time)
    open_asset_paths: List[AssetEntry] = field(default_factory=list)
    primary_asset_path: str = ""
    active_graph_path: str = ""
    selected_nodes: List[str] = field(default_factory=list)
    current_map: str = ""
    selected_actors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "captured_at": self.captured_at,
            "open_asset_paths": [entry.to_dict() for entry in self.open_asset_paths],
            "primary_asset_path": self.primary_asset_path,
            "active_graph_path": self.active_graph_path,
            "selected_nodes": list(self.selected_nodes),
            "current_map": self.current_map,
            "selected_actors": list(self.selected_actors),
        }


def _coerce_entry(raw: Any) -> Optional[AssetEntry]:
    if isinstance(raw, str):
        path = raw.strip()
        if not path:
            return None
        return AssetEntry(asset_path=path)
    if isinstance(raw, dict):
        path = str(raw.get("asset_path") or raw.get("path") or "").strip()
        if not path:
            return None
        return AssetEntry(
            asset_path=path,
            asset_class=str(raw.get("asset_class") or raw.get("class") or "").strip(),
            is_primary=bool(raw.get("is_primary")),
        )
    return None


def normalize_snapshot(payload: Dict[str, Any]) -> SessionSnapshot:
    """Accept a loose dict (from JSON or the C++ layer) and return a snapshot.

    Unknown keys are ignored.  Empty / missing fields get safe defaults.
    """

    entries: List[AssetEntry] = []
    seen: set = set()
    for raw in payload.get("open_asset_paths", []) or []:
        entry = _coerce_entry(raw)
        if entry and entry.asset_path not in seen:
            entries.append(entry)
            seen.add(entry.asset_path)

    primary = str(payload.get("primary_asset_path") or "").strip()
    if primary:
        if primary not in seen:
            entries.insert(0, AssetEntry(asset_path=primary, is_primary=True))
            seen.add(primary)
        else:
            for entry in entries:
                entry.is_primary = entry.asset_path == primary

    captured_raw = payload.get("captured_at")
    try:
        captured = float(captured_raw) if captured_raw is not None else time.time()
    except (TypeError, ValueError):
        captured = time.time()

    schema = payload.get("schema_version") or SCHEMA_VERSION
    try:
        schema_int = int(schema)
    except (TypeError, ValueError):
        schema_int = SCHEMA_VERSION

    def _coerce_str_list(value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, str):
            value = [value]
        out: List[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out

    return SessionSnapshot(
        schema_version=schema_int,
        captured_at=captured,
        open_asset_paths=entries,
        primary_asset_path=primary,
        active_graph_path=str(payload.get("active_graph_path") or "").strip(),
        selected_nodes=_coerce_str_list(payload.get("selected_nodes")),
        current_map=str(payload.get("current_map") or "").strip(),
        selected_actors=_coerce_str_list(payload.get("selected_actors")),
    )


def filter_by_policy(snapshot: SessionSnapshot, policy: str) -> SessionSnapshot:
    """Return a copy of the snapshot with fields stripped per restore policy."""

    if policy not in RESTORE_POLICIES:
        raise ValueError(f"unknown restore policy: {policy!r}")

    clone = copy.deepcopy(snapshot)
    if policy == "none":
        clone.open_asset_paths = []
        clone.primary_asset_path = ""
        clone.active_graph_path = ""
        clone.selected_nodes = []
        clone.selected_actors = []
        return clone

    if policy == "assets_only":
        # Strip tab/layout-level details and actor selection; keep assets +
        # primary focus so the editor can re-open the main working surface.
        clone.selected_actors = []
        return clone

    return clone


def classify_restore_targets(
    snapshot: SessionSnapshot,
    supported_classes: Iterable[str] = SUPPORTED_ASSET_CLASSES,
) -> Tuple[List[AssetEntry], List[AssetEntry]]:
    """Split the entries into (restorable, skipped).

    ``restorable`` preserves original ordering; ``skipped`` contains entries
    whose ``asset_class`` is known but unsupported.  Entries with an empty
    class default to restorable so the C++ layer can still try to open them
    (useful when the snapshot came from an older schema).
    """

    allowed = {cls.lower() for cls in supported_classes}
    restorable: List[AssetEntry] = []
    skipped: List[AssetEntry] = []
    for entry in snapshot.open_asset_paths:
        cls = (entry.asset_class or "").lower()
        if cls and cls not in allowed:
            skipped.append(entry)
        else:
            restorable.append(entry)
    return restorable, skipped


def build_restore_report(
    restored: Iterable[Dict[str, Any]],
    failed: Iterable[Dict[str, Any]],
    skipped: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    """Return the standard ``restored_assets``/``failed_assets`` response block."""

    return {
        "restored_assets": list(restored),
        "failed_assets": list(failed),
        "skipped_assets": list(skipped),
    }
