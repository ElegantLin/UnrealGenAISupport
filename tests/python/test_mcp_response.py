import importlib
import json
import sys
from types import ModuleType

from Content.Python.utils.mcp_response import API_VERSION, err, ok


def _load_mcp_server(monkeypatch, tmp_path):
    sys.modules.pop("Content.Python.mcp_server", None)

    fastmcp = ModuleType("fastmcp")

    class FastMCP:
        def __init__(self, *_args, **_kwargs):
            pass

        def tool(self, *_args, **_kwargs):
            def decorator(func):
                return func

            return decorator

    class Image:
        def __init__(self, *_args, **_kwargs):
            pass

    fastmcp.FastMCP = FastMCP
    fastmcp.Image = Image

    mss = ModuleType("mss")

    class _MSSContext:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def shot(self, *_args, **_kwargs):
            return None

    mss.mss = lambda: _MSSContext()

    monkeypatch.setitem(sys.modules, "fastmcp", fastmcp)
    monkeypatch.setitem(sys.modules, "mss", mss)
    monkeypatch.setenv("HOME", str(tmp_path))

    return importlib.import_module("Content.Python.mcp_server")


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


def test_ok_envelope_keeps_warning_list():
    payload = ok("ready", warnings=["legacy path"])
    assert payload["warnings"] == ["legacy path"]


def test_ok_preserves_explicit_falsy_data_and_warnings_values():
    payload = ok("ready", data=0, warnings="")
    assert payload["data"] == 0
    assert payload["warnings"] == ""


def test_reserved_envelope_fields_cannot_be_overridden_by_extras():
    ok_payload = ok(
        "ready",
        success=False,
        api_version="bad-version",
    )
    err_payload = err(
        "boom",
        success=True,
        error="not-boom",
        api_version="bad-version",
        error_code="bad-code",
        warnings=False,
    )
    assert ok_payload["success"] is True
    assert ok_payload["api_version"] == API_VERSION
    assert ok_payload["warnings"] == []
    assert err_payload["success"] is False
    assert err_payload["error"] == "boom"
    assert err_payload["api_version"] == API_VERSION
    assert err_payload["error_code"] == "bad-code"
    assert err_payload["warnings"] is False


def test_execute_python_script_wraps_success_payload(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    monkeypatch.setattr(mcp_server, "is_potentially_destructive", lambda _script: False)
    monkeypatch.setattr(
        mcp_server,
        "send_to_unreal",
        lambda command: {
            "success": True,
            "message": "Remote execution complete.",
            "output": "done",
            "type": command["type"],
        },
    )

    payload = mcp_server.execute_python_script("print('done')")

    assert payload["success"] is True
    assert payload["message"] == "Script executed successfully."
    assert payload["data"]["tool"] == "execute_python_script"
    assert payload["data"]["output"] == "done"
    assert payload["data"]["response"]["type"] == "execute_python"


def test_execute_python_script_preserves_partial_output_on_error(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    monkeypatch.setattr(mcp_server, "is_potentially_destructive", lambda _script: False)
    monkeypatch.setattr(
        mcp_server,
        "send_to_unreal",
        lambda _command: {
            "success": False,
            "error": "Script execution failed.",
            "output": "partial log output",
        },
    )

    payload = mcp_server.execute_python_script("print('broken')")

    assert payload["success"] is False
    assert payload["error"] == "Script execution failed."
    assert payload["error_code"] == "EXECUTE_PYTHON_FAILED"
    assert payload["data"]["tool"] == "execute_python_script"
    assert payload["data"]["output"] == "partial log output"
    assert payload["data"]["response"]["output"] == "partial log output"


def test_execute_python_script_confirmation_payload(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    monkeypatch.setattr(mcp_server, "is_potentially_destructive", lambda _script: True)

    payload = mcp_server.execute_python_script("unreal.EditorAssetLibrary.save_asset('/Game/Test')")

    assert payload["success"] is False
    assert payload["error_code"] == "CONFIRMATION_REQUIRED"
    assert payload["confirmation_required"] is True
    assert payload["data"]["tool"] == "execute_python_script"
    assert payload["data"]["script"] == "unreal.EditorAssetLibrary.save_asset('/Game/Test')"


def test_execute_python_script_pending_job_payload(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    monkeypatch.setattr(mcp_server, "is_potentially_destructive", lambda _script: False)
    monkeypatch.setattr(
        mcp_server,
        "send_to_unreal",
        lambda _command: {
            "success": True,
            "status": "running",
            "job_id": "job-123",
            "message": "Command 'execute_python' is running. Poll get_job_status.",
            "result_available": False,
        },
    )

    payload = mcp_server.execute_python_script("print('done')")

    assert payload["success"] is True
    assert payload["pending"] is True
    assert payload["job_id"] == "job-123"
    assert payload["status"] == "running"
    assert payload["message"] == "Command 'execute_python' is running. Poll get_job_status."


def test_execute_unreal_command_wraps_success_payload(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mcp_server,
        "send_to_unreal",
        lambda command: {
            "success": True,
            "output": "Stat unit output",
            "type": command["type"],
        },
    )

    payload = mcp_server.execute_unreal_command("stat unit")

    assert payload["success"] is True
    assert payload["message"] == "Command executed successfully."
    assert payload["data"]["tool"] == "execute_unreal_command"
    assert payload["data"]["command"] == "stat unit"
    assert payload["data"]["output"] == "Stat unit output"
    assert payload["data"]["response"]["type"] == "execute_unreal_command"


def test_execute_unreal_command_rejects_py_commands(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)

    payload = mcp_server.execute_unreal_command("py print('hi')")

    assert payload["success"] is False
    assert payload["error_code"] == "INVALID_COMMAND"
    assert payload["data"]["tool"] == "execute_unreal_command"
    assert payload["data"]["command"] == "py print('hi')"


def test_execute_unreal_command_confirmation_payload(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)

    payload = mcp_server.execute_unreal_command("save all")

    assert payload["success"] is False
    assert payload["error_code"] == "CONFIRMATION_REQUIRED"
    assert payload["confirmation_required"] is True
    assert payload["data"]["tool"] == "execute_unreal_command"
    assert payload["data"]["command"] == "save all"


def test_add_component_with_events_accepts_dict_response(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mcp_server,
        "send_to_unreal",
        lambda _command: {
            "success": True,
            "message": "Added component TriggerBox.",
            "events": {
                "begin_guid": "BEGIN_GUID",
                "end_guid": "END_GUID",
            },
        },
    )

    payload = mcp_server.add_component_with_events(
        "/Game/Blueprints/BP_Test",
        "TriggerBox",
        "BoxComponent",
    )

    assert payload["success"] is True
    assert payload["message"] == "Added component TriggerBox."
    assert payload["data"]["tool"] == "add_component_with_events"
    assert payload["data"]["events"]["begin_guid"] == "BEGIN_GUID"
    assert payload["data"]["events"]["end_guid"] == "END_GUID"


def test_restart_editor_confirmation_payload(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    restart_result = {
        "success": False,
        "confirmation_required": True,
        "reason": "unsaved_changes",
        "error": "Unreal Editor has unsaved assets or maps.",
        "dirty_packages": ["/Game/Maps/TestMap", "/Game/Blueprints/BP_Test"],
        "dirty_package_count": 2,
        "suggested_retry": "restart_editor(force=True)",
    }
    monkeypatch.setattr(mcp_server, "_restart_editor_impl", lambda *_args, **_kwargs: restart_result)

    payload = mcp_server.restart_editor()

    assert payload["success"] is False
    assert payload["error_code"] == "RESTART_CONFIRMATION_REQUIRED"
    assert payload["confirmation_required"] is True
    assert payload["reason"] == "unsaved_changes"
    assert payload["dirty_packages"] == restart_result["dirty_packages"]
    assert payload["dirty_package_count"] == 2
    assert payload["suggested_retry"] == "restart_editor(force=True)"
    assert payload["data"]["tool"] == "restart_editor"
    assert payload["data"]["response"] == restart_result


def test_enable_plugin_and_restart_confirmation_payload(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    enable_response = {
        "success": True,
        "message": "Enabled plugin 'ModelingToolsEditorMode'.",
        "restart_required": True,
    }
    restart_result = {
        "success": False,
        "confirmation_required": True,
        "reason": "unsaved_changes",
        "error": "Unreal Editor has unsaved assets or maps.",
        "dirty_packages": ["/Game/Maps/TestMap"],
        "dirty_package_count": 1,
        "suggested_retry": "restart_editor(force=True)",
    }
    monkeypatch.setattr(mcp_server, "send_to_unreal", lambda _command: enable_response)
    monkeypatch.setattr(mcp_server, "_restart_editor_impl", lambda *_args, **_kwargs: restart_result)

    payload = mcp_server.enable_plugin_and_restart("ModelingToolsEditorMode")

    assert payload["success"] is False
    assert payload["error_code"] == "RESTART_CONFIRMATION_REQUIRED"
    assert payload["confirmation_required"] is True
    assert payload["dirty_packages"] == restart_result["dirty_packages"]
    assert payload["dirty_package_count"] == 1
    assert payload["suggested_retry"] == "restart_editor(force=True)"
    assert payload["data"]["tool"] == "enable_plugin_and_restart"
    assert payload["data"]["enable_response"] == enable_response
    assert payload["data"]["restart_response"] == restart_result


def test_get_capabilities_wraps_unreal_response(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    response = {
        "success": True,
        "capabilities": ["execute_python", "restart_editor"],
    }
    monkeypatch.setattr(mcp_server, "send_to_unreal", lambda _command: response)

    payload = mcp_server.get_capabilities()

    assert payload["success"] is True
    assert payload["message"] == "Capabilities loaded."
    assert payload["data"] == response


def test_get_capabilities_preserves_normalized_failure_diagnostics(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mcp_server,
        "send_to_unreal",
        lambda _command: "{invalid json",
    )

    payload = mcp_server.get_capabilities()

    assert payload["success"] is False
    assert payload["error_code"] == "INVALID_UNREAL_RESPONSE"
    assert payload["data"]["raw_response"] == "{invalid json"
    assert "Failed to parse Unreal response" in payload["error"]


def test_get_capabilities_accepts_json_string_response(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    response = {
        "success": True,
        "capabilities": ["execute_python", "restart_editor"],
    }
    monkeypatch.setattr(
        mcp_server,
        "send_to_unreal",
        lambda _command: json.dumps(response),
    )

    payload = mcp_server.get_capabilities()

    assert payload["success"] is True
    assert payload["message"] == "Capabilities loaded."
    assert payload["data"] == response


def test_preflight_project_returns_existing_envelope(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    response = {
        "success": True,
        "message": "Preflight complete.",
        "data": {
            "ok": False,
            "failed_checks": [{"name": "architecture_compatibility"}],
        },
        "warnings": ["Could not determine the project target architecture."],
        "api_version": API_VERSION,
    }
    monkeypatch.setattr(mcp_server, "send_to_unreal", lambda _command: response)

    payload = mcp_server.preflight_project()

    assert payload == response


def test_get_job_status_wraps_socket_response(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    response = {
        "success": True,
        "job_id": "job-123",
        "status": "running",
        "progress": 0.25,
    }
    monkeypatch.setattr(mcp_server, "send_to_unreal", lambda _command: response)

    payload = mcp_server.get_job_status("job-123")

    assert payload["success"] is True
    assert payload["message"] == "Job status loaded."
    assert payload["data"]["job_id"] == "job-123"
    assert payload["data"]["status"] == "running"


def test_converted_tool_docstrings_describe_structured_envelope(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)

    for tool_name in (
        "execute_python_script",
        "execute_unreal_command",
        "add_component_with_events",
        "restart_editor",
        "enable_plugin_and_restart",
        "preflight_project",
        "get_job_status",
        "cancel_job",
        "list_active_jobs",
    ):
        docstring = getattr(mcp_server, tool_name).__doc__ or ""
        assert "Structured response envelope dict" in docstring


def test_how_to_use_returns_structured_payload(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)

    payload = mcp_server.how_to_use()

    assert payload["success"] is True
    assert payload["data"]["tool"] == "how_to_use"
    assert "Fresh Session Rule" in payload["data"]["content"]


def test_handshake_test_wraps_success_payload(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mcp_server,
        "send_to_unreal",
        lambda _command: {
            "success": True,
            "message": "Received: hello",
            "connection_info": {"session_id": "UE-123"},
        },
    )

    payload = mcp_server.handshake_test("hello")

    assert payload["success"] is True
    assert payload["message"] == "Handshake successful."
    assert payload["data"]["tool"] == "handshake_test"
    assert payload["data"]["response"]["connection_info"]["session_id"] == "UE-123"


def test_create_blueprint_wraps_success_payload(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mcp_server,
        "send_to_unreal",
        lambda _command: {
            "success": True,
            "blueprint_path": "/Game/Blueprints/BP_Test",
        },
    )

    payload = mcp_server.create_blueprint("BP_Test")

    assert payload["success"] is True
    assert payload["message"] == "Blueprint created successfully."
    assert payload["data"]["tool"] == "create_blueprint"
    assert payload["data"]["blueprint_path"] == "/Game/Blueprints/BP_Test"


def test_get_all_scene_objects_wraps_actor_list(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mcp_server,
        "send_to_unreal",
        lambda _command: {
            "success": True,
            "actors": [{"name": "Cube_1", "class": "StaticMeshActor"}],
        },
    )

    payload = mcp_server.get_all_scene_objects()

    assert payload["success"] is True
    assert payload["message"] == "Scene objects loaded."
    assert payload["data"]["tool"] == "get_all_scene_objects"
    assert payload["data"]["actors"][0]["name"] == "Cube_1"


def test_search_free_fab_assets_wraps_verified_results(monkeypatch, tmp_path):
    mcp_server = _load_mcp_server(monkeypatch, tmp_path)
    monkeypatch.setattr(
        mcp_server,
        "_resolve_fab_search",
        lambda query, max_results, timeout_seconds: {
            "success": True,
            "status": "completed",
            "query": query,
            "results": [{"listing_id": "asset-1", "title": "Stylized Tree Pack"}],
            "max_results": max_results,
            "timeout_seconds": timeout_seconds,
        },
    )

    payload = mcp_server.search_free_fab_assets("tree")

    assert payload["success"] is True
    assert payload["message"] == "Fab search completed."
    assert payload["data"]["tool"] == "search_free_fab_assets"
    assert payload["data"]["results"][0]["listing_id"] == "asset-1"
