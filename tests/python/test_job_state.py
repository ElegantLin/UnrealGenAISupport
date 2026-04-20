from Content.Python.utils.job_state import (
    JobRecord,
    mark_cancelled,
    mark_completed,
    mark_failed,
    mark_running,
)


def test_mark_running_sets_status_and_progress():
    record = JobRecord(job_id="job-1", command_type="compile_blueprint")

    updated = mark_running(record, progress=0.25)

    assert updated.status == "running"
    assert updated.progress == 0.25


def test_terminal_transitions_preserve_result_metadata():
    record = JobRecord(job_id="job-1", command_type="execute_python")

    completed = mark_completed(
        record,
        result={"success": True, "output": "done"},
        recent_logs=["log line"],
        warnings=["warning line"],
    )
    failed = mark_failed(record, "boom", result={"success": False})
    cancelled = mark_cancelled(record)

    assert completed.status == "completed"
    assert completed.result["output"] == "done"
    assert completed.recent_logs == ["log line"]
    assert completed.warnings == ["warning line"]
    assert failed.status == "failed"
    assert failed.error == "boom"
    assert cancelled.status == "cancelled"
