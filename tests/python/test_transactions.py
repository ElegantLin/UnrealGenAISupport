from Content.Python.utils.transactions import (
    TERMINAL_STATUSES,
    TransactionRegistry,
    build_mutation_report,
    mark_applied,
    mark_failed,
    mark_preview_ready,
    mark_rolled_back,
    mark_verified,
    new_transaction,
    to_dict,
)


def test_new_transaction_starts_pending():
    record = new_transaction("set_property", target_assets=["/Game/A", "/Game/A", "  "])
    assert record.status == "pending"
    assert record.target_assets == ["/Game/A"]
    assert record.transaction_id.startswith("tx-")


def test_lifecycle_pending_to_verified():
    record = new_transaction("compile_blueprint", target_assets=["/Game/B"])
    record = mark_preview_ready(
        record,
        preview={"changes": ["x"]},
        warnings=["existing dirty asset"],
        snapshot_token="snap-1",
    )
    assert record.status == "preview_ready"
    assert record.snapshot_token == "snap-1"
    assert record.warnings == ["existing dirty asset"]

    record = mark_applied(record, result={"saved_assets": ["/Game/B"]}, warnings=["soft warn"])
    assert record.status == "applied"
    assert record.result["saved_assets"] == ["/Game/B"]
    assert "soft warn" in record.warnings

    record = mark_verified(record, verification_checks=[{"name": "reload", "ok": True}])
    assert record.status == "verified"
    assert record.verification_checks == [{"name": "reload", "ok": True}]
    assert record.status in TERMINAL_STATUSES


def test_failed_records_error_and_keeps_warnings():
    record = mark_failed(new_transaction("set_property"), "boom", warnings=["w"])
    assert record.status == "failed"
    assert record.error == "boom"
    assert record.warnings == ["w"]


def test_rolled_back_sets_flag():
    record = mark_rolled_back(new_transaction("set_property"), "save failed")
    assert record.status == "rolled_back"
    assert record.rollback_performed is True
    assert record.error == "save failed"


def test_build_mutation_report_has_stable_keys():
    record = mark_applied(
        new_transaction("set_property", target_assets=["/Game/X"]),
        result={"changed": True},
    )
    report = build_mutation_report(
        record,
        changed_assets=["/Game/X"],
        compiled_assets=[],
        saved_assets=["/Game/X"],
        extra={"diagnostic_bundle": "/Saved/MCP/foo.json"},
    )
    for key in (
        "transaction_id",
        "operation",
        "status",
        "target_assets",
        "changed_assets",
        "compiled_assets",
        "saved_assets",
        "warnings",
        "rollback_performed",
        "verification_checks",
    ):
        assert key in report
    assert report["diagnostic_bundle"] == "/Saved/MCP/foo.json"


def test_registry_tracks_and_returns_last_applied():
    registry = TransactionRegistry()
    a = registry.register(new_transaction("set_property"))
    b = registry.register(new_transaction("compile_blueprint"))
    registry.update(mark_applied(a))
    assert registry.last_applied().transaction_id == a.transaction_id
    registry.update(mark_applied(b))
    assert registry.last_applied().transaction_id == b.transaction_id
    registry.update(mark_failed(b, "bad"))
    assert registry.last_applied().transaction_id == a.transaction_id


def test_to_dict_roundtrip_keys():
    record = new_transaction("set_property")
    payload = to_dict(record)
    assert payload["transaction_id"] == record.transaction_id
    assert payload["status"] == "pending"
