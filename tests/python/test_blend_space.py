import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

from utils.blend_space import (  # noqa: E402
    AxisConfig,
    BlendSample,
    BlendSpaceError,
    BlendSpaceInfo,
    build_reload_report,
    diff_sample_lists,
    normalize_axis,
    normalize_sample,
    validate_samples,
)


def _axis(name: str, lo: float = 0.0, hi: float = 1.0) -> AxisConfig:
    return AxisConfig(name=name, min_value=lo, max_value=hi)


def test_normalize_axis_happy():
    axis = normalize_axis({"name": "Speed", "min": 0.0, "max": 600.0, "kind": "speed"})
    assert axis.name == "Speed"
    assert axis.kind == "Speed"
    assert axis.max_value == 600.0


def test_normalize_axis_rejects_bad_bounds():
    with pytest.raises(BlendSpaceError):
        normalize_axis({"name": "X", "min_value": 1.0, "max_value": 0.0})


def test_normalize_axis_rejects_unknown_kind():
    with pytest.raises(BlendSpaceError):
        normalize_axis({"name": "X", "min_value": 0, "max_value": 1, "kind": "Bogus"})


def test_normalize_sample_requires_animation_path():
    with pytest.raises(BlendSpaceError):
        normalize_sample({"coordinates": [0, 0]}, axis_count=2)


def test_normalize_sample_accepts_xy_shortcut():
    s = normalize_sample({"animation_path": "/Game/A", "x": 1, "y": 2}, axis_count=2)
    assert s.coordinates == (1.0, 2.0)


def test_normalize_sample_enforces_axis_count():
    with pytest.raises(BlendSpaceError):
        normalize_sample(
            {"animation_path": "/Game/A", "coordinates": [0]}, axis_count=2
        )


def test_validate_samples_detects_oob_and_duplicates():
    axes = [_axis("X", 0, 1), _axis("Y", 0, 1)]
    samples = [
        BlendSample(animation_path="/Game/A", coordinates=(0.5, 0.5)),
        BlendSample(animation_path="/Game/B", coordinates=(2.0, 0.5)),
        BlendSample(animation_path="/Game/A", coordinates=(0.5, 0.5)),
    ]
    problems = validate_samples(samples, axes)
    assert any("outside" in p for p in problems)
    assert any("duplicates" in p for p in problems)


def test_validate_samples_empty_flagged():
    assert validate_samples([], [_axis("X")])[0] == "no samples provided"


def test_diff_sample_lists_tracks_add_remove():
    prev = [BlendSample(animation_path="/Game/A", coordinates=(0, 0))]
    curr = [BlendSample(animation_path="/Game/B", coordinates=(0, 0))]
    diff = diff_sample_lists(prev, curr)
    assert len(diff["added"]) == 1 and diff["added"][0].animation_path == "/Game/B"
    assert len(diff["removed"]) == 1 and diff["removed"][0].animation_path == "/Game/A"


def test_build_reload_report_reload_failed():
    info = BlendSpaceInfo(blend_space_path="/Game/BS")
    report = build_reload_report(info, None)
    assert report["reloaded"] is False
    assert report["error_code"] == "BLEND_SPACE_RELOAD_FAILED"


def test_build_reload_report_samples_match():
    sample = BlendSample(animation_path="/Game/A", coordinates=(0.0, 0.0))
    before = BlendSpaceInfo(blend_space_path="/Game/BS", samples=[sample])
    after = BlendSpaceInfo(blend_space_path="/Game/BS", samples=[sample])
    report = build_reload_report(before, after)
    assert report["reloaded"] is True
    assert report["sample_count_match"] is True
    assert report["missing_after_reload"] == []
    assert report["unexpected_after_reload"] == []
