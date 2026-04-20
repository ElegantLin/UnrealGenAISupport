from dataclasses import asdict, dataclass, field, replace
from time import time
from typing import Any, Dict, List


def _clamp_progress(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    command_type: str
    status: str = "queued"
    progress: float = 0.0
    cancellable: bool = True
    result: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    recent_logs: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time)
    updated_at: float = field(default_factory=time)


def mark_running(record: JobRecord, progress: float = 0.0) -> JobRecord:
    return replace(
        record,
        status="running",
        progress=_clamp_progress(progress),
        updated_at=time(),
    )


def mark_completed(
    record: JobRecord,
    *,
    result: Dict[str, Any] | None = None,
    recent_logs: List[str] | None = None,
    warnings: List[str] | None = None,
) -> JobRecord:
    return replace(
        record,
        status="completed",
        progress=1.0,
        result={} if result is None else result,
        recent_logs=[] if recent_logs is None else recent_logs,
        warnings=[] if warnings is None else warnings,
        error="",
        updated_at=time(),
    )


def mark_failed(
    record: JobRecord,
    error: str,
    *,
    result: Dict[str, Any] | None = None,
    recent_logs: List[str] | None = None,
    warnings: List[str] | None = None,
) -> JobRecord:
    return replace(
        record,
        status="failed",
        result={} if result is None else result,
        error=error,
        recent_logs=[] if recent_logs is None else recent_logs,
        warnings=[] if warnings is None else warnings,
        updated_at=time(),
    )


def mark_cancelled(record: JobRecord, message: str = "Job cancelled.") -> JobRecord:
    return replace(
        record,
        status="cancelled",
        error=message,
        updated_at=time(),
    )


def to_dict(record: JobRecord) -> Dict[str, Any]:
    return asdict(record)
