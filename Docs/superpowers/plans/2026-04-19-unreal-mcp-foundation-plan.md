# Unreal MCP Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `P0A + P0B` foundation for the Unreal MCP so every tool returns a stable structured result, environment problems are detected before execution, and long-running commands no longer depend on a fixed blocking timeout.

**Architecture:** Introduce a small pure-Python foundation layer for response envelopes, preflight helpers, and job state so the protocol contract can be tested outside Unreal. Keep Unreal-specific inspection in handlers, route all client-visible tool results through the shared envelope, and let the socket server own job state, progress, and cancellation.

**Tech Stack:** Python 3.11, FastMCP, Unreal Python API, pytest, existing plugin socket transport

---

## Scope Decision

The umbrella roadmap in [unreal-mcp-improvement-plan.md](/Users/zonglindi/Documents/UnrealGenAISupport/unreal-mcp-improvement-plan.md:1) spans multiple independent subsystems. This plan intentionally covers only the first executable slice:

- `P0A` Protocol Contract
- `P0B` Execution Model And Preflight

Follow-on slices should be planned separately:

- Slice 2: `P1 + P1.25`
- Slice 3: `P1.5 + P2 + P3`
- Slice 4: `P4 + P5`
- Slice 5: `P6 + P7`

## File Structure

**Create**
- `Content/Python/utils/mcp_response.py`
  - Shared response envelope helpers and `api_version` constant.
- `Content/Python/utils/job_state.py`
  - Pure-Python job record helpers and status transitions.
- `Content/Python/handlers/preflight_commands.py`
  - Project preflight orchestration and architecture mismatch formatting.
- `tests/python/test_mcp_response.py`
  - Unit tests for response envelope builders.
- `tests/python/test_job_state.py`
  - Unit tests for job lifecycle helpers.
- `tests/python/test_preflight_commands.py`
  - Unit tests for pure preflight helper logic and mismatch formatting.

**Modify**
- `Content/Python/mcp_server.py`
  - Normalize all outward-facing tool results, add `get_capabilities`, and add job inspection tools.
- `Content/Python/unreal_socket_server.py`
  - Replace fixed blocking queue behavior with tracked jobs, progress, and cancellation.
- `Content/Python/handlers/plugin_commands.py`
  - Expand `get_editor_context`, wire preflight, and expose capability-related handler data.
- `Content/Python/utils/logging.py`
  - Add command-scoped log snapshot helpers for jobs.
- `Content/Python/knowledge_base/how_to_use.md`
  - Document new safe/unsafe contract, job usage, and preflight expectations.
- `README.md`
  - Document the structured result contract and new foundation capabilities.

## Task 1: Add The Shared Response Envelope

**Files:**
- Create: `Content/Python/utils/mcp_response.py`
- Test: `tests/python/test_mcp_response.py`

- [ ] **Step 1: Write the failing response envelope test**

```python
from Content.Python.utils.mcp_response import API_VERSION, err, ok


def test_ok_envelope_has_required_fields():
    payload = ok("ready", data={"tool": "ping"})
    assert payload == {
        "success": True,
        "message": "ready",
        "data": {"tool": "ping"},
        "warnings": [],
        "api_version": API_VERSION,
    }


def test_err_envelope_has_error_code_and_error_text():
    payload = err("boom", error_code="TEST_FAILURE")
    assert payload["success"] is False
    assert payload["error"] == "boom"
    assert payload["error_code"] == "TEST_FAILURE"
    assert payload["api_version"] == API_VERSION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_mcp_response.py -q`
Expected: FAIL with `ModuleNotFoundError` for `Content.Python.utils.mcp_response`

- [ ] **Step 3: Write the minimal shared envelope helper**

```python
API_VERSION = "2026-04-19"


def ok(message="", data=None, warnings=None, **extra):
    payload = {
        "success": True,
        "message": message,
        "data": data or {},
        "warnings": warnings or [],
        "api_version": API_VERSION,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def err(message, error_code="UNKNOWN_ERROR", warnings=None, **extra):
    payload = {
        "success": False,
        "message": message,
        "error": message,
        "error_code": error_code,
        "warnings": warnings or [],
        "api_version": API_VERSION,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_mcp_response.py -q`
Expected: PASS with `2 passed`

- [ ] **Step 5: Commit**

```bash
git add Content/Python/utils/mcp_response.py tests/python/test_mcp_response.py
git commit -m "feat: add shared MCP response envelope"
```

## Task 2: Normalize `mcp_server.py` Around The Envelope

**Files:**
- Modify: `Content/Python/mcp_server.py`
- Test: `tests/python/test_mcp_response.py`

- [ ] **Step 1: Write a failing adapter test for string-only tool output**

```python
from Content.Python.utils.mcp_response import ok


def test_tool_adapter_wraps_success_payload():
    payload = ok("done", data={"tool": "execute_python"})
    assert payload["success"] is True
    assert payload["data"]["tool"] == "execute_python"
```

- [ ] **Step 2: Run the targeted test**

Run: `python -m pytest tests/python/test_mcp_response.py -q`
Expected: PASS for envelope test, but no adapter coverage yet

- [ ] **Step 3: Refactor the highest-risk tools first**

Apply the envelope to these tool functions in `Content/Python/mcp_server.py`:

```python
def _tool_success(message, data=None, **extra):
    return ok(message=message, data=data, **extra)


def _tool_error(message, error_code="TOOL_ERROR", **extra):
    return err(message=message, error_code=error_code, **extra)
```

Refactor these first because they currently expose inconsistent shapes:

- `execute_python_script`
- `execute_unreal_command`
- `add_component_with_events`
- `restart_editor`
- `enable_plugin_and_restart`

- [ ] **Step 4: Add `get_capabilities` to `mcp_server.py`**

Use this tool signature:

```python
@mcp.tool()
def get_capabilities() -> dict:
    response = send_to_unreal({"type": "get_capabilities"})
    if response.get("success"):
        return ok("Capabilities loaded.", data=response)
    return err(response.get("error", "Failed to load capabilities."), error_code=response.get("error_code", "CAPABILITIES_FAILED"))
```

- [ ] **Step 5: Verify syntax after the refactor**

Run: `python -m compileall Content/Python/mcp_server.py Content/Python/utils/mcp_response.py`
Expected: `Compiling 'Content/Python/mcp_server.py'...` with no syntax errors

- [ ] **Step 6: Commit**

```bash
git add Content/Python/mcp_server.py Content/Python/utils/mcp_response.py
git commit -m "refactor: normalize MCP tool responses"
```

## Task 3: Add Project Preflight And Richer Editor Context

**Files:**
- Create: `Content/Python/handlers/preflight_commands.py`
- Modify: `Content/Python/handlers/plugin_commands.py`
- Test: `tests/python/test_preflight_commands.py`

- [ ] **Step 1: Write the failing pure helper tests**

```python
from Content.Python.handlers.preflight_commands import build_arch_mismatch_warning


def test_build_arch_mismatch_warning_mentions_editor_and_module_arch():
    message = build_arch_mismatch_warning("arm64", ["x64"])
    assert "arm64" in message
    assert "x64" in message
    assert "rebuild" in message.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_preflight_commands.py -q`
Expected: FAIL with `ModuleNotFoundError` for `preflight_commands`

- [ ] **Step 3: Implement the pure preflight helpers**

```python
def build_arch_mismatch_warning(editor_arch, module_architectures):
    module_text = ", ".join(sorted(set(module_architectures))) or "unknown"
    return (
        f"Architecture mismatch: editor is {editor_arch} but modules are {module_text}. "
        "Rebuild the affected binaries for the active editor architecture."
    )


def summarize_preflight_checks(checks):
    failed = [check for check in checks if not check["ok"]]
    return {"ok": not failed, "checks": checks, "failed_checks": failed}
```

- [ ] **Step 4: Add `handle_preflight_project`**

Implement in `Content/Python/handlers/preflight_commands.py` and register from `Content/Python/unreal_socket_server.py`:

```python
def handle_preflight_project(command):
    checks = [
        {"name": "uproject", "ok": bool(project_file_path)},
        {"name": "editor_path", "ok": bool(editor_path)},
        {"name": "plugin_state", "ok": True},
    ]
    return ok("Preflight complete.", data=summarize_preflight_checks(checks))
```

- [ ] **Step 5: Expand `handle_get_editor_context`**

Add these fields in `Content/Python/handlers/plugin_commands.py`:

```python
"editor_binary_architecture": editor_binary_architecture,
"project_target_architecture": project_target_architecture,
"module_architectures": module_architectures,
"input_system": input_system,
"enabled_plugins": enabled_plugins,
"dirty_assets": dirty_assets,
"open_asset_paths": open_asset_paths,
```

- [ ] **Step 6: Run tests and syntax checks**

Run: `python -m pytest tests/python/test_preflight_commands.py -q`
Expected: PASS with `1 passed`

Run: `python -m compileall Content/Python/handlers/preflight_commands.py Content/Python/handlers/plugin_commands.py`
Expected: compile output with no syntax errors

- [ ] **Step 7: Commit**

```bash
git add Content/Python/handlers/preflight_commands.py Content/Python/handlers/plugin_commands.py tests/python/test_preflight_commands.py
git commit -m "feat: add MCP preflight and richer editor context"
```

## Task 4: Introduce Job State And Socket-Level Job Tracking

**Files:**
- Create: `Content/Python/utils/job_state.py`
- Modify: `Content/Python/unreal_socket_server.py`
- Test: `tests/python/test_job_state.py`

- [ ] **Step 1: Write the failing job state tests**

```python
from Content.Python.utils.job_state import JobRecord, mark_running


def test_mark_running_sets_status_and_progress():
    record = JobRecord(job_id="job-1", command_type="compile_blueprint")
    updated = mark_running(record, progress=0.25)
    assert updated.status == "running"
    assert updated.progress == 0.25
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_job_state.py -q`
Expected: FAIL with `ModuleNotFoundError` for `job_state`

- [ ] **Step 3: Implement the pure job model**

```python
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    command_type: str
    status: str = "queued"
    progress: float = 0.0
    cancellable: bool = True


def mark_running(record, progress=0.0):
    return replace(record, status="running", progress=progress)
```

- [ ] **Step 4: Refactor the socket server to store jobs by `job_id`**

Minimum behavior in `Content/Python/unreal_socket_server.py`:

- Generate a `job_id` for every non-handshake command.
- Store status transitions: `queued -> running -> completed|failed|cancelled`.
- Return immediate job metadata for long-running commands.
- Add command handlers for `get_job_status`, `cancel_job`, and `list_active_jobs`.

- [ ] **Step 5: Run unit tests and syntax checks**

Run: `python -m pytest tests/python/test_job_state.py -q`
Expected: PASS with `1 passed`

Run: `python -m compileall Content/Python/unreal_socket_server.py Content/Python/utils/job_state.py`
Expected: compile output with no syntax errors

- [ ] **Step 6: Commit**

```bash
git add Content/Python/utils/job_state.py Content/Python/unreal_socket_server.py tests/python/test_job_state.py
git commit -m "feat: add MCP job tracking model"
```

## Task 5: Add Command-Scoped Diagnostics And Capability Surfacing

**Files:**
- Modify: `Content/Python/utils/logging.py`
- Modify: `Content/Python/unreal_socket_server.py`
- Modify: `Content/Python/mcp_server.py`
- Test: `tests/python/test_mcp_response.py`

- [ ] **Step 1: Add a failing diagnostics test for warnings passthrough**

```python
from Content.Python.utils.mcp_response import ok


def test_ok_envelope_keeps_warning_list():
    payload = ok("ready", warnings=["legacy path"])
    assert payload["warnings"] == ["legacy path"]
```

- [ ] **Step 2: Run the targeted tests**

Run: `python -m pytest tests/python/test_mcp_response.py -q`
Expected: PASS for existing cases, fail if warnings passthrough is missing

- [ ] **Step 3: Add log snapshot helpers**

Add helper shape in `Content/Python/utils/logging.py`:

```python
def begin_command_log_snapshot():
    return get_log_line_count()


def end_command_log_snapshot(start_line):
    return get_recent_unreal_logs(start_line)
```

- [ ] **Step 4: Attach diagnostics to job completion**

When a job completes in `Content/Python/unreal_socket_server.py`, attach:

```python
{
    "job_id": job_id,
    "recent_logs": recent_logs,
    "warnings": warnings,
}
```

Also ensure `get_capabilities` includes:

- `unsafe_commands`
- `supported_graph_types`
- `supported_asset_types`
- `api_version`

- [ ] **Step 5: Run syntax and test verification**

Run: `python -m pytest tests/python/test_mcp_response.py -q`
Expected: PASS

Run: `python -m compileall Content/Python/utils/logging.py Content/Python/unreal_socket_server.py Content/Python/mcp_server.py`
Expected: compile output with no syntax errors

- [ ] **Step 6: Commit**

```bash
git add Content/Python/utils/logging.py Content/Python/unreal_socket_server.py Content/Python/mcp_server.py tests/python/test_mcp_response.py
git commit -m "feat: attach diagnostics to MCP jobs"
```

## Task 6: Update Docs And Run Foundation Verification

**Files:**
- Modify: `README.md`
- Modify: `Content/Python/knowledge_base/how_to_use.md`

- [ ] **Step 1: Document the structured result contract**

Add a short contract section to `README.md` covering:

- envelope fields
- `get_capabilities`
- `preflight_project`
- job status flow
- unsafe command markers

- [ ] **Step 2: Update the LLM-facing knowledge base**

Add short guidance to `Content/Python/knowledge_base/how_to_use.md`:

- always call `get_capabilities` or `preflight_project` first in a fresh session
- prefer job polling for long-running operations
- treat `execute_python` as unsafe

- [ ] **Step 3: Run repo-local verification**

Run: `python -m pytest tests/python -q`
Expected: PASS for all new unit tests

Run: `python -m compileall Content/Python`
Expected: compile output with no syntax errors

- [ ] **Step 4: Run manual Unreal verification**

Manual checklist:

- Start the socket server in Unreal.
- Call `get_capabilities` and confirm `api_version`, `unsafe_commands`, and asset/graph support arrays appear.
- Call `preflight_project` and confirm it reports editor path and architecture fields.
- Start a command that takes longer than a trivial getter and confirm `job_id` can be polled.
- Force a failure and confirm `recent_logs` comes back in the result.

- [ ] **Step 5: Commit**

```bash
git add README.md Content/Python/knowledge_base/how_to_use.md
git commit -m "docs: document MCP foundation contract"
```

## Exit Criteria For This Plan

- All outward-facing MCP tools use the shared response envelope or are on a short explicit migration list.
- `get_capabilities`, `preflight_project`, `get_job_status`, `cancel_job`, and `list_active_jobs` exist and return structured results.
- `get_editor_context` exposes richer architecture and environment data.
- The socket server no longer depends on a fixed 10-second blocking wait as its primary long-running execution model.
- New pure-Python tests pass locally.
- Manual Unreal validation confirms capabilities, preflight, jobs, and diagnostics work end-to-end.
