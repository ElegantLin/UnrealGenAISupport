"""Observability helpers: structured mutation reports and diagnostic bundles.

These helpers are designed to be importable both under Unreal (where
``Content/Python`` is on ``sys.path``) and under pytest (via the
``Content.Python`` namespace) without touching the filesystem unless the
caller explicitly asks for that side-effect.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional


# ---------------------------------------------------------------------------
# Mutation report ------------------------------------------------------------
# ---------------------------------------------------------------------------


@dataclass
class MutationReport:
    """Structured mutation report returned alongside every write response.

    Field definitions match the spec in ``unreal-mcp-status-and-test-plan.md``
    section P6.
    """

    changed_assets: List[str] = field(default_factory=list)
    compiled_assets: List[str] = field(default_factory=list)
    saved_assets: List[str] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    rollback_performed: bool = False
    verification_checks: List[Dict[str, Any]] = field(default_factory=list)
    diff_summary: Dict[str, Any] = field(default_factory=dict)
    log_delta: List[str] = field(default_factory=list)
    diagnostic_bundle: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "changed_assets": list(self.changed_assets),
            "compiled_assets": list(self.compiled_assets),
            "saved_assets": list(self.saved_assets),
            "warnings": list(self.warnings),
            "rollback_performed": self.rollback_performed,
            "verification_checks": list(self.verification_checks),
            "diff_summary": dict(self.diff_summary),
            "log_delta": list(self.log_delta),
            "diagnostic_bundle": self.diagnostic_bundle,
        }


def empty_report() -> MutationReport:
    return MutationReport()


def merge_reports(primary: MutationReport, secondary: MutationReport) -> MutationReport:
    """Merge two mutation reports, preserving deduplicated order."""

    def _dedup(seq: Iterable[str]) -> List[str]:
        seen: Dict[str, None] = {}
        for item in seq:
            if item not in seen:
                seen[item] = None
        return list(seen.keys())

    return MutationReport(
        changed_assets=_dedup(list(primary.changed_assets) + list(secondary.changed_assets)),
        compiled_assets=_dedup(list(primary.compiled_assets) + list(secondary.compiled_assets)),
        saved_assets=_dedup(list(primary.saved_assets) + list(secondary.saved_assets)),
        warnings=list(primary.warnings) + list(secondary.warnings),
        rollback_performed=primary.rollback_performed or secondary.rollback_performed,
        verification_checks=list(primary.verification_checks) + list(secondary.verification_checks),
        diff_summary={**primary.diff_summary, **secondary.diff_summary},
        log_delta=list(primary.log_delta) + list(secondary.log_delta),
        diagnostic_bundle=primary.diagnostic_bundle or secondary.diagnostic_bundle,
    )


# ---------------------------------------------------------------------------
# Diagnostic bundle persistence ---------------------------------------------
# ---------------------------------------------------------------------------


def _default_bundle_dir() -> str:
    """Resolve ``<ProjectSaved>/MCP/diagnostics`` when running inside Unreal.

    Falls back to a local directory when the runtime is not available so the
    helpers remain usable from pytest without side-effects on disk unless
    the caller explicitly passes ``base_dir``.
    """
    try:
        import unreal  # type: ignore

        project_saved = unreal.Paths.project_saved_dir()  # type: ignore[attr-defined]
        return os.path.join(project_saved, "MCP", "diagnostics")
    except Exception:
        return os.path.join(os.getcwd(), "Saved", "MCP", "diagnostics")


def _safe_name(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in text)
    return cleaned[:64] or "op"


def build_bundle(
    operation: str,
    request_payload: Dict[str, Any],
    normalized_arguments: Dict[str, Any],
    report: MutationReport,
    log_delta: Optional[List[str]] = None,
    verify_results: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
    timestamp: Optional[float] = None,
) -> Dict[str, Any]:
    """Build a diagnostic bundle dict. Does NOT touch the filesystem."""
    ts = timestamp if timestamp is not None else time.time()
    return {
        "schema_version": 1,
        "operation": operation,
        "timestamp": ts,
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
        "request_payload": request_payload,
        "normalized_arguments": normalized_arguments,
        "mutation_report": report.to_dict() if isinstance(report, MutationReport) else report,
        "log_delta": list(log_delta or []),
        "verify_results": dict(verify_results or {}),
        "extra": dict(extra or {}),
    }


def persist_bundle(
    bundle: Dict[str, Any],
    base_dir: Optional[str] = None,
) -> str:
    """Write ``bundle`` to disk and return the path. Best-effort; raises on IO."""
    target_dir = base_dir or _default_bundle_dir()
    os.makedirs(target_dir, exist_ok=True)
    operation = _safe_name(str(bundle.get("operation") or "op"))
    timestamp = bundle.get("timestamp") or time.time()
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime(timestamp))
    filename = f"{stamp}_{operation}.json"
    path = os.path.join(target_dir, filename)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(bundle, handle, indent=2, sort_keys=True)
    return path


# ---------------------------------------------------------------------------
# Envelope decoration -------------------------------------------------------
# ---------------------------------------------------------------------------


def attach_report(response: Dict[str, Any], report: MutationReport) -> Dict[str, Any]:
    """Return a copy of ``response`` with ``mutation_report`` attached.

    * If ``response["data"]`` exists, we nest the report there to keep the
      envelope root clean.
    * If the response is an ``err(...)`` payload, we attach the report at
      top level so diagnostics survive the failure.
    """
    if not isinstance(response, dict):
        return response
    payload = dict(response)
    report_dict = report.to_dict() if isinstance(report, MutationReport) else report
    if payload.get("success"):
        data = dict(payload.get("data") or {})
        data["mutation_report"] = report_dict
        payload["data"] = data
    else:
        payload["mutation_report"] = report_dict
    return payload
