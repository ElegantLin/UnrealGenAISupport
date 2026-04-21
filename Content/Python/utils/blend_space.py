"""Pure helpers for BlendSpace read/write (P3).

BlendSpaces have a lot of magic in their internal ``GridSamples`` layout; we
keep validation / normalization here so the safe-write handlers can stay
declarative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SUPPORTED_AXIS_KINDS: Tuple[str, ...] = ("Speed", "Direction", "Angle", "Custom")


class BlendSpaceError(ValueError):
    """Raised for invalid BlendSpace payloads."""


@dataclass
class AxisConfig:
    name: str
    min_value: float
    max_value: float
    grid_divisions: int = 4
    smoothing_time: float = 0.0
    kind: str = "Custom"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "grid_divisions": int(self.grid_divisions),
            "smoothing_time": float(self.smoothing_time),
            "kind": self.kind,
        }


@dataclass
class BlendSample:
    animation_path: str
    coordinates: Tuple[float, ...]
    rate_scale: float = 1.0
    mirror: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "animation_path": self.animation_path,
            "coordinates": list(self.coordinates),
            "rate_scale": float(self.rate_scale),
            "mirror": bool(self.mirror),
        }


@dataclass
class BlendSpaceInfo:
    blend_space_path: str
    skeleton_path: str = ""
    is_additive: bool = False
    axes: List[AxisConfig] = field(default_factory=list)
    samples: List[BlendSample] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blend_space_path": self.blend_space_path,
            "skeleton_path": self.skeleton_path,
            "is_additive": bool(self.is_additive),
            "axes": [axis.to_dict() for axis in self.axes],
            "samples": [sample.to_dict() for sample in self.samples],
        }


def normalize_axis(raw: Dict[str, Any]) -> AxisConfig:
    if not isinstance(raw, dict):
        raise BlendSpaceError("axis must be a dict")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise BlendSpaceError("axis.name is required")

    try:
        min_val = float(raw.get("min_value", raw.get("min", 0.0)))
        max_val = float(raw.get("max_value", raw.get("max", 0.0)))
    except (TypeError, ValueError) as exc:
        raise BlendSpaceError(f"axis {name!r} has non-numeric bounds") from exc
    if max_val <= min_val:
        raise BlendSpaceError(
            f"axis {name!r} max_value ({max_val}) must be greater than min_value ({min_val})"
        )

    grid = int(raw.get("grid_divisions", raw.get("divisions", 4)) or 4)
    if grid < 1:
        raise BlendSpaceError(f"axis {name!r} grid_divisions must be >= 1")

    kind = str(raw.get("kind") or "Custom")
    for candidate in SUPPORTED_AXIS_KINDS:
        if candidate.lower() == kind.lower():
            kind = candidate
            break
    else:
        raise BlendSpaceError(f"axis {name!r} has unsupported kind {kind!r}")

    smoothing = float(raw.get("smoothing_time", 0.0) or 0.0)
    if smoothing < 0.0:
        raise BlendSpaceError(f"axis {name!r} smoothing_time must be >= 0")

    return AxisConfig(
        name=name,
        min_value=min_val,
        max_value=max_val,
        grid_divisions=grid,
        smoothing_time=smoothing,
        kind=kind,
    )


def normalize_sample(
    raw: Dict[str, Any],
    axis_count: int,
) -> BlendSample:
    if not isinstance(raw, dict):
        raise BlendSpaceError("sample must be a dict")
    anim = str(raw.get("animation_path") or raw.get("animation") or "").strip()
    if not anim:
        raise BlendSpaceError("sample.animation_path is required")

    coords_raw = raw.get("coordinates")
    if coords_raw is None:
        x = raw.get("x")
        y = raw.get("y")
        coords_raw = [c for c in (x, y) if c is not None]
    if not isinstance(coords_raw, (list, tuple)):
        raise BlendSpaceError("sample.coordinates must be a list")
    try:
        coords = tuple(float(c) for c in coords_raw)
    except (TypeError, ValueError) as exc:
        raise BlendSpaceError("sample.coordinates must be numeric") from exc
    if axis_count and len(coords) != axis_count:
        raise BlendSpaceError(
            f"sample for {anim!r} has {len(coords)} coordinates but BlendSpace has {axis_count} axes"
        )

    rate = float(raw.get("rate_scale", 1.0) or 1.0)
    return BlendSample(
        animation_path=anim,
        coordinates=coords,
        rate_scale=rate,
        mirror=bool(raw.get("mirror", False)),
    )


def validate_samples(
    samples: Sequence[BlendSample],
    axes: Sequence[AxisConfig],
) -> List[str]:
    """Return a list of human-readable problems; empty list = OK."""

    problems: List[str] = []
    if not samples:
        problems.append("no samples provided")
        return problems

    seen: set = set()
    for idx, sample in enumerate(samples):
        if len(sample.coordinates) != len(axes):
            problems.append(
                f"sample #{idx} ({sample.animation_path}) has {len(sample.coordinates)} coordinates but BlendSpace has {len(axes)} axes"
            )
            continue
        for coord, axis in zip(sample.coordinates, axes):
            if coord < axis.min_value or coord > axis.max_value:
                problems.append(
                    f"sample #{idx} ({sample.animation_path}) value {coord} on axis {axis.name!r} outside [{axis.min_value},{axis.max_value}]"
                )
        signature = (sample.animation_path, sample.coordinates)
        if signature in seen:
            problems.append(
                f"sample #{idx} duplicates an earlier entry for {sample.animation_path} at {sample.coordinates}"
            )
        seen.add(signature)
    return problems


def diff_sample_lists(
    previous: Iterable[BlendSample],
    current: Iterable[BlendSample],
) -> Dict[str, List[BlendSample]]:
    """Coarse diff used by post-save reload verification."""

    prev_list = list(previous)
    curr_list = list(current)
    prev_keys = {(s.animation_path, s.coordinates) for s in prev_list}
    curr_keys = {(s.animation_path, s.coordinates) for s in curr_list}
    added = [s for s in curr_list if (s.animation_path, s.coordinates) not in prev_keys]
    removed = [s for s in prev_list if (s.animation_path, s.coordinates) not in curr_keys]
    return {"added": added, "removed": removed}


def build_reload_report(
    before: BlendSpaceInfo,
    after: Optional[BlendSpaceInfo],
) -> Dict[str, Any]:
    """Compose the post-save verification report."""

    if after is None:
        return {
            "reloaded": False,
            "error_code": "BLEND_SPACE_RELOAD_FAILED",
            "message": "Failed to reload BlendSpace after save",
        }
    sample_count_match = len(before.samples) == len(after.samples)
    diff = diff_sample_lists(before.samples, after.samples)
    return {
        "reloaded": True,
        "sample_count_match": sample_count_match,
        "sample_count_before": len(before.samples),
        "sample_count_after": len(after.samples),
        "missing_after_reload": [s.to_dict() for s in diff["removed"]],
        "unexpected_after_reload": [s.to_dict() for s in diff["added"]],
    }
