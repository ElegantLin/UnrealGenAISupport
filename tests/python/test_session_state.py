import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

from utils.session_state import (  # noqa: E402
    AssetEntry,
    SessionSnapshot,
    build_restore_report,
    classify_restore_targets,
    filter_by_policy,
    normalize_snapshot,
)


def test_normalize_snapshot_adds_missing_primary():
    snap = normalize_snapshot({"primary_asset_path": "/Game/MyBP", "open_asset_paths": []})
    assert snap.primary_asset_path == "/Game/MyBP"
    assert len(snap.open_asset_paths) == 1
    assert snap.open_asset_paths[0].is_primary is True


def test_normalize_snapshot_marks_primary_inside_list():
    snap = normalize_snapshot(
        {
            "primary_asset_path": "/Game/A",
            "open_asset_paths": [
                {"asset_path": "/Game/B", "asset_class": "Blueprint"},
                "/Game/A",
            ],
        }
    )
    paths = [(e.asset_path, e.is_primary) for e in snap.open_asset_paths]
    assert ("/Game/A", True) in paths
    assert ("/Game/B", False) in paths


def test_normalize_snapshot_deduplicates_and_filters_empty():
    snap = normalize_snapshot(
        {
            "open_asset_paths": ["/Game/A", "", "/Game/A", {"asset_path": "  "}],
        }
    )
    assert [e.asset_path for e in snap.open_asset_paths] == ["/Game/A"]


def test_filter_by_policy_none_strips_everything():
    snap = normalize_snapshot(
        {
            "open_asset_paths": ["/Game/A"],
            "primary_asset_path": "/Game/A",
            "active_graph_path": "EventGraph",
            "selected_nodes": ["node-1"],
            "current_map": "/Game/Map",
            "selected_actors": ["Actor1"],
        }
    )
    filtered = filter_by_policy(snap, "none")
    assert filtered.open_asset_paths == []
    assert filtered.primary_asset_path == ""
    assert filtered.active_graph_path == ""
    assert filtered.selected_actors == []
    # Original snapshot should be untouched.
    assert snap.open_asset_paths


def test_filter_by_policy_assets_only_clears_actor_selection():
    snap = normalize_snapshot(
        {"open_asset_paths": ["/Game/A"], "selected_actors": ["Actor1"]}
    )
    filtered = filter_by_policy(snap, "assets_only")
    assert filtered.open_asset_paths and filtered.open_asset_paths[0].asset_path == "/Game/A"
    assert filtered.selected_actors == []


def test_filter_by_policy_rejects_unknown():
    snap = SessionSnapshot()
    try:
        filter_by_policy(snap, "bogus")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for bogus policy")


def test_classify_restore_targets_skips_unsupported_class():
    snap = normalize_snapshot(
        {
            "open_asset_paths": [
                {"asset_path": "/Game/A", "asset_class": "Blueprint"},
                {"asset_path": "/Game/B", "asset_class": "StaticMesh"},
                {"asset_path": "/Game/C", "asset_class": ""},
            ],
        }
    )
    restorable, skipped = classify_restore_targets(snap)
    assert [e.asset_path for e in restorable] == ["/Game/A", "/Game/C"]
    assert [e.asset_path for e in skipped] == ["/Game/B"]


def test_build_restore_report_shape():
    report = build_restore_report(
        restored=[{"asset_path": "/Game/A"}],
        failed=[{"asset_path": "/Game/B", "reason": "missing"}],
        skipped=[{"asset_path": "/Game/C", "reason": "unsupported"}],
    )
    assert set(report.keys()) == {"restored_assets", "failed_assets", "skipped_assets"}
    assert report["failed_assets"][0]["reason"] == "missing"
