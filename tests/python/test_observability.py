import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

from utils import error_codes  # noqa: E402
from utils.mcp_response import err, ok  # noqa: E402
from utils.observability import (  # noqa: E402
    MutationReport,
    attach_report,
    build_bundle,
    empty_report,
    merge_reports,
    persist_bundle,
)


def test_error_codes_catalogue_is_complete():
    for code in (
        "ARCH_MISMATCH",
        "PROTOCOL_SHAPE_MISMATCH",
        "JOB_TIMEOUT",
        "PIN_INCOMPATIBLE",
        "NODE_NOT_FOUND",
        "GRAPH_NOT_SUPPORTED",
        "ASSET_VALIDATION_FAILED",
        "SAVE_FAILED",
        "ROLLBACK_FAILED",
        "LEGACY_INPUT_PATH",
        "UNSAFE_COMMAND_REQUIRED",
    ):
        assert code in error_codes.ALL_ERROR_CODES, f"missing {code}"


def test_mutation_report_merge_dedups():
    a = MutationReport(changed_assets=["/Game/A"], warnings=[{"message": "x"}])
    b = MutationReport(changed_assets=["/Game/A", "/Game/B"], rollback_performed=True)
    merged = merge_reports(a, b)
    assert merged.changed_assets == ["/Game/A", "/Game/B"]
    assert merged.rollback_performed is True
    assert merged.warnings == [{"message": "x"}]


def test_attach_report_places_under_data_for_ok():
    response = ok("done", data={"result": 1})
    report = MutationReport(changed_assets=["/Game/A"])
    decorated = attach_report(response, report)
    assert decorated["success"] is True
    assert decorated["data"]["mutation_report"]["changed_assets"] == ["/Game/A"]


def test_attach_report_on_err_places_at_root():
    response = err("boom", error_code="SAVE_FAILED")
    report = MutationReport(changed_assets=["/Game/A"], rollback_performed=True)
    decorated = attach_report(response, report)
    assert decorated["success"] is False
    assert decorated["mutation_report"]["rollback_performed"] is True


def test_build_bundle_shape():
    bundle = build_bundle(
        operation="create_state",
        request_payload={"state_machine": "Locomotion", "state": "Walk"},
        normalized_arguments={"state_machine": "Locomotion"},
        report=empty_report(),
        log_delta=["LogBP: compile ok"],
        verify_results={"nodes_added": 1},
        timestamp=1_700_000_000.0,
    )
    assert bundle["operation"] == "create_state"
    assert bundle["timestamp_iso"].startswith("2023-")
    assert bundle["log_delta"] == ["LogBP: compile ok"]
    assert "mutation_report" in bundle


def test_persist_bundle_writes_file(tmp_path):
    bundle = build_bundle(
        operation="create_state",
        request_payload={},
        normalized_arguments={},
        report=empty_report(),
        timestamp=1_700_000_000.0,
    )
    path = persist_bundle(bundle, base_dir=str(tmp_path))
    assert os.path.isfile(path)
    with open(path, "r", encoding="utf-8") as fh:
        reloaded = json.load(fh)
    assert reloaded["operation"] == "create_state"
