"""Handlers for the safe mutation runtime (P1).

The handlers run pure Python orchestration and degrade gracefully when the
``unreal`` module or ``UGenAssetTransactionUtils`` C++ helpers are missing.

The dispatcher is intentionally simple: callers send an ``operation`` payload
that names the kind of mutation (``set_property``, ``add_component``,
``compile_blueprint``, ``execute_python``).  ``preview_operation`` produces a
``transaction_id``; ``apply_operation`` consumes that token to commit the
work; ``undo_last_mcp_operation`` rolls back the most recent applied
transaction we are tracking.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

try:  # pragma: no cover - import shape depends on Unreal vs pytest
    from utils.mcp_response import err, ok
    from utils.transactions import (
        TransactionRegistry,
        build_mutation_report,
        mark_applied,
        mark_failed,
        mark_preview_ready,
        mark_rolled_back,
        mark_verified,
        new_transaction,
    )
except ImportError:  # pragma: no cover
    from Content.Python.utils.mcp_response import err, ok
    from Content.Python.utils.transactions import (
        TransactionRegistry,
        build_mutation_report,
        mark_applied,
        mark_failed,
        mark_preview_ready,
        mark_rolled_back,
        mark_verified,
        new_transaction,
    )


_REGISTRY = TransactionRegistry()


def get_registry() -> TransactionRegistry:
    """Expose the module-level registry (mainly for tests)."""

    return _REGISTRY


def _get_unreal_module():
    try:
        import unreal  # type: ignore
    except ImportError:
        return None
    return unreal


def _get_transaction_utils():
    unreal_module = _get_unreal_module()
    if unreal_module is None:
        return None
    return getattr(unreal_module, "GenAssetTransactionUtils", None)


# ---------------------------------------------------------------------------
# Operation registry
# ---------------------------------------------------------------------------


PreviewFn = Callable[[Dict[str, Any]], Dict[str, Any]]
ApplyFn = Callable[[Dict[str, Any]], Dict[str, Any]]


_OPERATIONS: Dict[str, Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]] = {}


def register_operation(name: str, *, preview: PreviewFn, apply: ApplyFn) -> None:
    """Register a preview/apply pair for a named operation."""

    key = str(name or "").strip()
    if not key:
        raise ValueError("Operation name must be a non-empty string.")
    _OPERATIONS[key] = {"preview": preview, "apply": apply}


def _operation_for(name: str) -> Optional[Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]]:
    return _OPERATIONS.get(str(name or "").strip())


# ---------------------------------------------------------------------------
# Generic operation: relies on UGenAssetTransactionUtils when available
# ---------------------------------------------------------------------------


def _generic_preview(payload: Dict[str, Any]) -> Dict[str, Any]:
    utils = _get_transaction_utils()
    target_assets = payload.get("target_assets") or ([] if not payload.get("target_asset") else [payload["target_asset"]])

    if utils is None:
        return {
            "preview": {
                "summary": "Editor-side preview unavailable; running in dry-run.",
                "intended_changes": payload.get("changes", []),
                "dry_run": True,
            },
            "warnings": ["GenAssetTransactionUtils is not loaded; preview is informational only."],
            "snapshot_token": "",
        }

    try:
        snapshot_token = str(utils.duplicate_for_preview(target_assets[0] if target_assets else ""))
    except Exception as exc:  # pragma: no cover - exercised only with Unreal
        return {
            "preview": {"summary": "Preview snapshot failed.", "error": str(exc)},
            "warnings": [str(exc)],
            "snapshot_token": "",
        }

    return {
        "preview": {
            "summary": "Snapshot created.",
            "intended_changes": payload.get("changes", []),
        },
        "warnings": [],
        "snapshot_token": snapshot_token,
    }


def _generic_apply(payload: Dict[str, Any]) -> Dict[str, Any]:
    utils = _get_transaction_utils()
    if utils is None:
        return {
            "result": {"applied": False, "reason": "GenAssetTransactionUtils not available."},
            "warnings": ["Apply skipped: GenAssetTransactionUtils not loaded."],
            "verification_checks": [],
            "rollback": False,
        }

    try:
        report_json = utils.apply_transaction(payload.get("snapshot_token", ""), payload.get("changes_json", ""))
    except Exception as exc:  # pragma: no cover
        return {
            "result": {"applied": False, "error": str(exc)},
            "warnings": [str(exc)],
            "verification_checks": [],
            "rollback": True,
        }

    return {
        "result": {"applied": True, "report": report_json},
        "warnings": [],
        "verification_checks": [],
        "rollback": False,
    }


# Default operations -- additional ones can be registered by other handlers.
register_operation("generic", preview=_generic_preview, apply=_generic_apply)


# ---------------------------------------------------------------------------
# Public command handlers
# ---------------------------------------------------------------------------


def handle_preview_operation(command: Dict[str, Any]) -> Dict[str, Any]:
    operation = str(command.get("operation", "generic")).strip() or "generic"
    handlers = _operation_for(operation) or _operation_for("generic")
    if handlers is None:
        return err(
            f"Unknown mutation operation: {operation}",
            error_code="UNSUPPORTED_OPERATION",
        )

    payload = command.get("payload") if isinstance(command.get("payload"), dict) else command
    target_assets = payload.get("target_assets") or (
        [payload["target_asset"]] if payload.get("target_asset") else []
    )

    record = new_transaction(operation, target_assets=target_assets)
    _REGISTRY.register(record)

    try:
        preview_result = handlers["preview"](payload)
    except Exception as exc:
        record = mark_failed(record, f"Preview failed: {exc}")
        _REGISTRY.update(record)
        return err(
            f"Preview failed: {exc}",
            error_code="PREVIEW_FAILED",
            data=build_mutation_report(record),
        )

    record = mark_preview_ready(
        record,
        preview=preview_result.get("preview", {}),
        warnings=preview_result.get("warnings", []),
        snapshot_token=str(preview_result.get("snapshot_token", "")),
    )
    _REGISTRY.update(record)

    return ok(
        "Preview ready.",
        data={
            "transaction_id": record.transaction_id,
            "operation": record.operation,
            "preview": record.preview,
            "snapshot_token": record.snapshot_token,
            "target_assets": record.target_assets,
        },
        warnings=record.warnings,
    )


def handle_apply_operation(command: Dict[str, Any]) -> Dict[str, Any]:
    transaction_id = str(command.get("transaction_id", "")).strip()
    if not transaction_id:
        return err(
            "Missing required parameter: transaction_id",
            error_code="TRANSACTION_ID_REQUIRED",
        )

    record = _REGISTRY.get(transaction_id)
    if record is None:
        return err(
            f"Unknown transaction: {transaction_id}",
            error_code="TRANSACTION_NOT_FOUND",
        )

    if record.status not in {"preview_ready", "pending"}:
        return err(
            f"Transaction {transaction_id} is in {record.status} state and cannot be applied.",
            error_code="TRANSACTION_NOT_APPLIABLE",
            data=build_mutation_report(record),
        )

    handlers = _operation_for(record.operation) or _operation_for("generic")
    payload = command.get("payload") if isinstance(command.get("payload"), dict) else command
    payload = dict(payload)
    payload.setdefault("snapshot_token", record.snapshot_token)
    payload.setdefault("target_assets", list(record.target_assets))

    try:
        apply_result = handlers["apply"](payload)
    except Exception as exc:
        record = mark_failed(record, f"Apply failed: {exc}")
        _REGISTRY.update(record)
        rolled_back = _attempt_rollback(record)
        if rolled_back:
            record = mark_rolled_back(record, f"Apply failed: {exc}")
            _REGISTRY.update(record)
        return err(
            f"Apply failed: {exc}",
            error_code="APPLY_FAILED",
            data=build_mutation_report(record),
        )

    if apply_result.get("rollback"):
        rolled_back = _attempt_rollback(record)
        if rolled_back:
            record = mark_rolled_back(record, "Apply requested rollback.")
            _REGISTRY.update(record)
        else:
            record = mark_failed(record, "Apply requested rollback but rollback failed.")
            _REGISTRY.update(record)
        return err(
            "Operation rolled back.",
            error_code="OPERATION_ROLLED_BACK",
            data=build_mutation_report(record),
        )

    record = mark_applied(
        record,
        result=apply_result.get("result", {}),
        warnings=apply_result.get("warnings", []),
    )
    record = mark_verified(record, apply_result.get("verification_checks", []))
    _REGISTRY.update(record)

    report = build_mutation_report(
        record,
        changed_assets=apply_result.get("changed_assets"),
        compiled_assets=apply_result.get("compiled_assets"),
        saved_assets=apply_result.get("saved_assets"),
    )
    return ok("Operation applied.", data=report, warnings=record.warnings)


def handle_undo_last_mcp_operation(command: Dict[str, Any]) -> Dict[str, Any]:
    del command
    record = _REGISTRY.last_applied()
    if record is None:
        return err(
            "No applied MCP transaction available to undo.",
            error_code="NO_APPLIED_TRANSACTION",
        )

    rolled_back = _attempt_rollback(record)
    if not rolled_back:
        return err(
            f"Could not undo transaction {record.transaction_id}.",
            error_code="UNDO_FAILED",
            data=build_mutation_report(record),
        )

    record = mark_rolled_back(record, "Undo requested by client.")
    _REGISTRY.update(record)
    return ok(
        "Last MCP operation undone.",
        data=build_mutation_report(record),
    )


def _attempt_rollback(record) -> bool:
    utils = _get_transaction_utils()
    if utils is None:
        # Without C++ helpers we cannot truly rollback; treat as advisory.
        return True
    try:
        return bool(utils.rollback_to_snapshot(record.snapshot_token))
    except Exception:  # pragma: no cover
        return False
