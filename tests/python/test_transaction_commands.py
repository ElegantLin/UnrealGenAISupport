import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _isolate_modules():
    sys.modules.pop("Content.Python.handlers.transaction_commands", None)
    sys.modules.pop("unreal", None)
    yield
    sys.modules.pop("Content.Python.handlers.transaction_commands", None)
    sys.modules.pop("unreal", None)


def _import_handlers(unreal_stub=None):
    if unreal_stub is not None:
        sys.modules["unreal"] = unreal_stub
    from Content.Python.handlers import transaction_commands

    # Reset module-level registry between tests
    transaction_commands._REGISTRY = transaction_commands.TransactionRegistry()
    return transaction_commands


def test_preview_with_no_unreal_returns_dry_run_warning():
    handlers = _import_handlers()
    response = handlers.handle_preview_operation(
        {"operation": "generic", "payload": {"target_assets": ["/Game/A"], "changes": [{"k": 1}]}}
    )
    assert response["success"] is True
    assert response["data"]["operation"] == "generic"
    assert "transaction_id" in response["data"]
    assert response["data"]["target_assets"] == ["/Game/A"]
    assert any("GenAssetTransactionUtils" in w for w in response["warnings"])


def test_apply_unknown_transaction_returns_structured_error():
    handlers = _import_handlers()
    response = handlers.handle_apply_operation({"transaction_id": "tx-missing"})
    assert response["success"] is False
    assert response["error_code"] == "TRANSACTION_NOT_FOUND"


def test_apply_requires_transaction_id():
    handlers = _import_handlers()
    response = handlers.handle_apply_operation({})
    assert response["success"] is False
    assert response["error_code"] == "TRANSACTION_ID_REQUIRED"


def test_full_lifecycle_with_stubbed_unreal_helpers():
    unreal_stub = types.ModuleType("unreal")

    class FakeTxUtils:
        @staticmethod
        def duplicate_for_preview(asset_path):
            return f"snap::{asset_path}"

        @staticmethod
        def apply_transaction(snapshot_token, changes_json):
            return f"applied::{snapshot_token}"

        @staticmethod
        def rollback_to_snapshot(snapshot_token):
            return True

    unreal_stub.GenAssetTransactionUtils = FakeTxUtils
    handlers = _import_handlers(unreal_stub)

    preview = handlers.handle_preview_operation(
        {"operation": "generic", "payload": {"target_asset": "/Game/A"}}
    )
    assert preview["success"] is True
    transaction_id = preview["data"]["transaction_id"]
    assert preview["data"]["snapshot_token"] == "snap::/Game/A"

    apply_response = handlers.handle_apply_operation({"transaction_id": transaction_id})
    assert apply_response["success"] is True
    report = apply_response["data"]
    assert report["status"] == "verified"
    assert report["rollback_performed"] is False

    undo_response = handlers.handle_undo_last_mcp_operation({})
    assert undo_response["success"] is True
    assert undo_response["data"]["status"] == "rolled_back"


def test_apply_failure_triggers_rollback():
    unreal_stub = types.ModuleType("unreal")

    class FakeTxUtils:
        @staticmethod
        def duplicate_for_preview(asset_path):
            return f"snap::{asset_path}"

        @staticmethod
        def apply_transaction(snapshot_token, changes_json):
            raise RuntimeError("boom")

        rollback_called = False

        @staticmethod
        def rollback_to_snapshot(snapshot_token):
            FakeTxUtils.rollback_called = True
            return True

    unreal_stub.GenAssetTransactionUtils = FakeTxUtils
    handlers = _import_handlers(unreal_stub)

    preview = handlers.handle_preview_operation(
        {"operation": "generic", "payload": {"target_asset": "/Game/A"}}
    )
    transaction_id = preview["data"]["transaction_id"]
    apply_response = handlers.handle_apply_operation({"transaction_id": transaction_id})
    assert apply_response["success"] is False
    # The generic apply handler turns the exception into ``rollback: True``,
    # so the dispatcher reports OPERATION_ROLLED_BACK rather than APPLY_FAILED.
    assert apply_response["error_code"] == "OPERATION_ROLLED_BACK"
    assert apply_response["data"]["rollback_performed"] is True
    assert FakeTxUtils.rollback_called is True


def test_undo_with_no_applied_returns_error():
    handlers = _import_handlers()
    response = handlers.handle_undo_last_mcp_operation({})
    assert response["success"] is False
    assert response["error_code"] == "NO_APPLIED_TRANSACTION"


def test_unknown_operation_falls_back_to_generic():
    handlers = _import_handlers()
    response = handlers.handle_preview_operation(
        {"operation": "totally_made_up", "payload": {"target_asset": "/Game/X"}}
    )
    assert response["success"] is True
    # The record should still record the original operation name even though
    # we executed the generic handlers.
    assert response["data"]["operation"] == "totally_made_up"
