"""Pure-Python transaction lifecycle helpers used by the safe mutation runtime.

These helpers do not depend on the ``unreal`` module so they can be unit
tested outside of the Editor.  The actual asset mutation work is delegated to
``UGenAssetTransactionUtils`` on the C++ side; this module only models the
state machine, the preview registry and the mutation report envelope.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from time import time
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4


# Status transitions:
#   pending -> preview_ready -> applied -> verified
#                            \-> failed
#                            \-> rolled_back
TERMINAL_STATUSES = frozenset({"verified", "failed", "rolled_back"})


def _normalize_strings(values: Optional[Iterable[Any]]) -> List[str]:
    if not values:
        return []
    seen: set[str] = set()
    out: List[str] = []
    for raw in values:
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


@dataclass(frozen=True)
class TransactionRecord:
    """Snapshot of a single MCP-originated mutation transaction."""

    transaction_id: str
    operation: str
    target_assets: List[str] = field(default_factory=list)
    status: str = "pending"
    preview: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    error: str = ""
    rollback_performed: bool = False
    verification_checks: List[Dict[str, Any]] = field(default_factory=list)
    snapshot_token: str = ""
    created_at: float = field(default_factory=time)
    updated_at: float = field(default_factory=time)


def new_transaction(
    operation: str,
    target_assets: Optional[Iterable[Any]] = None,
    *,
    transaction_id: Optional[str] = None,
) -> TransactionRecord:
    return TransactionRecord(
        transaction_id=transaction_id or f"tx-{uuid4().hex[:12]}",
        operation=str(operation or "").strip() or "unknown",
        target_assets=_normalize_strings(target_assets),
    )


def mark_preview_ready(
    record: TransactionRecord,
    preview: Optional[Dict[str, Any]] = None,
    *,
    warnings: Optional[Iterable[Any]] = None,
    snapshot_token: str = "",
) -> TransactionRecord:
    return replace(
        record,
        status="preview_ready",
        preview=dict(preview or {}),
        warnings=_normalize_strings(warnings) or list(record.warnings),
        snapshot_token=snapshot_token or record.snapshot_token,
        updated_at=time(),
    )


def mark_applied(
    record: TransactionRecord,
    *,
    result: Optional[Dict[str, Any]] = None,
    warnings: Optional[Iterable[Any]] = None,
) -> TransactionRecord:
    merged_warnings = list(record.warnings)
    for w in _normalize_strings(warnings):
        if w not in merged_warnings:
            merged_warnings.append(w)
    return replace(
        record,
        status="applied",
        result=dict(result or {}),
        warnings=merged_warnings,
        updated_at=time(),
    )


def mark_verified(
    record: TransactionRecord,
    verification_checks: Optional[Iterable[Dict[str, Any]]] = None,
) -> TransactionRecord:
    return replace(
        record,
        status="verified",
        verification_checks=[dict(c) for c in (verification_checks or [])],
        updated_at=time(),
    )


def mark_failed(
    record: TransactionRecord,
    error: str,
    *,
    warnings: Optional[Iterable[Any]] = None,
    verification_checks: Optional[Iterable[Dict[str, Any]]] = None,
) -> TransactionRecord:
    merged_warnings = list(record.warnings)
    for w in _normalize_strings(warnings):
        if w not in merged_warnings:
            merged_warnings.append(w)
    return replace(
        record,
        status="failed",
        error=str(error or "").strip() or "Transaction failed.",
        warnings=merged_warnings,
        verification_checks=(
            [dict(c) for c in verification_checks]
            if verification_checks is not None
            else list(record.verification_checks)
        ),
        updated_at=time(),
    )


def mark_rolled_back(
    record: TransactionRecord,
    error: str = "",
) -> TransactionRecord:
    return replace(
        record,
        status="rolled_back",
        rollback_performed=True,
        error=str(error or "").strip() or record.error,
        updated_at=time(),
    )


def to_dict(record: TransactionRecord) -> Dict[str, Any]:
    return asdict(record)


def build_mutation_report(
    record: TransactionRecord,
    *,
    changed_assets: Optional[Iterable[Any]] = None,
    compiled_assets: Optional[Iterable[Any]] = None,
    saved_assets: Optional[Iterable[Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compose the standard mutation report payload.

    Always includes ``warnings``, ``rollback_performed`` and
    ``verification_checks`` even when empty so the schema is stable.
    """

    report: Dict[str, Any] = {
        "transaction_id": record.transaction_id,
        "operation": record.operation,
        "status": record.status,
        "target_assets": list(record.target_assets),
        "changed_assets": _normalize_strings(changed_assets),
        "compiled_assets": _normalize_strings(compiled_assets),
        "saved_assets": _normalize_strings(saved_assets),
        "warnings": list(record.warnings),
        "rollback_performed": bool(record.rollback_performed),
        "verification_checks": [dict(c) for c in record.verification_checks],
    }
    if record.error:
        report["error"] = record.error
    if extra:
        for key, value in extra.items():
            if key not in report and value is not None:
                report[key] = value
    return report


class TransactionRegistry:
    """Process-local registry of transactions keyed by ``transaction_id``."""

    def __init__(self) -> None:
        self._records: Dict[str, TransactionRecord] = {}
        self._history: List[str] = []

    def __contains__(self, transaction_id: str) -> bool:
        return transaction_id in self._records

    def register(self, record: TransactionRecord) -> TransactionRecord:
        self._records[record.transaction_id] = record
        self._history.append(record.transaction_id)
        return record

    def get(self, transaction_id: str) -> Optional[TransactionRecord]:
        return self._records.get(transaction_id)

    def update(self, record: TransactionRecord) -> TransactionRecord:
        self._records[record.transaction_id] = record
        return record

    def discard(self, transaction_id: str) -> None:
        self._records.pop(transaction_id, None)

    def last_applied(self) -> Optional[TransactionRecord]:
        for transaction_id in reversed(self._history):
            record = self._records.get(transaction_id)
            if record and record.status in {"applied", "verified"}:
                return record
        return None

    def all_records(self) -> List[TransactionRecord]:
        return [self._records[tid] for tid in self._history if tid in self._records]
