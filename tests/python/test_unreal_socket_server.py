import importlib
import sys
import threading
from types import ModuleType, SimpleNamespace


def _load_unreal_socket_server(monkeypatch):
    sys.modules.pop("Content.Python.unreal_socket_server", None)

    class DummyThread:
        def __init__(self, *args, **kwargs):
            self.daemon = False

        def start(self):
            return None

    monkeypatch.setattr(threading, "Thread", DummyThread)

    unreal = ModuleType("unreal")
    unreal.SystemLibrary = SimpleNamespace(get_engine_version=lambda: "5.4")
    unreal.register_slate_post_tick_callback = lambda _callback: None
    monkeypatch.setitem(sys.modules, "unreal", unreal)

    def _success(_command):
        return {"success": True}

    handler_specs = {
        "basic_commands": [
            "handle_spawn",
            "handle_create_material",
            "handle_take_screenshot",
            "handle_get_all_scene_objects",
            "handle_create_project_folder",
            "handle_get_files_in_folder",
            "handle_add_input_binding",
        ],
        "actor_commands": [
            "handle_modify_object",
            "handle_edit_component_property",
            "handle_add_component_with_events",
        ],
        "blueprint_commands": [
            "handle_create_blueprint",
            "handle_add_component",
            "handle_add_variable",
            "handle_add_function",
            "handle_add_node",
            "handle_connect_nodes",
            "handle_compile_blueprint",
            "handle_spawn_blueprint",
            "handle_delete_node",
            "handle_get_node_guid",
            "handle_get_all_nodes",
            "handle_get_node_suggestions",
            "handle_add_nodes_bulk",
            "handle_connect_nodes_bulk",
        ],
        "plugin_commands": [
            "handle_get_editor_context",
            "handle_set_plugin_enabled",
            "handle_request_editor_restart",
        ],
        "preflight_commands": [
            "handle_preflight_project",
        ],
        "python_commands": [
            "handle_execute_python",
            "handle_execute_unreal_command",
        ],
        "ui_commands": [
            "handle_add_widget_to_user_widget",
            "handle_edit_widget_property",
        ],
        "fab_commands": [
            "handle_start_fab_search",
            "handle_start_fab_add_to_project",
            "handle_get_fab_operation_status",
        ],
    }

    handlers = ModuleType("handlers")
    monkeypatch.setitem(sys.modules, "handlers", handlers)
    for module_name, function_names in handler_specs.items():
        module = ModuleType(f"handlers.{module_name}")
        for function_name in function_names:
            setattr(module, function_name, _success)
        setattr(handlers, module_name, module)
        monkeypatch.setitem(sys.modules, f"handlers.{module_name}", module)

    from Content.Python.utils import job_state as actual_job_state
    from Content.Python.utils import mcp_response as actual_mcp_response

    utils = ModuleType("utils")
    logging_module = ModuleType("utils.logging")
    logging_module.log_info = lambda *_args, **_kwargs: None
    logging_module.log_warning = lambda *_args, **_kwargs: None
    logging_module.log_error = lambda *_args, **_kwargs: None
    logging_module.begin_command_log_snapshot = lambda: 0
    logging_module.end_command_log_snapshot = lambda _start_line: []

    setattr(utils, "logging", logging_module)
    setattr(utils, "job_state", actual_job_state)
    setattr(utils, "mcp_response", actual_mcp_response)

    monkeypatch.setitem(sys.modules, "utils", utils)
    monkeypatch.setitem(sys.modules, "utils.logging", logging_module)
    monkeypatch.setitem(sys.modules, "utils.job_state", actual_job_state)
    monkeypatch.setitem(sys.modules, "utils.mcp_response", actual_mcp_response)

    return importlib.import_module("Content.Python.unreal_socket_server")


def test_long_running_commands_default_to_immediate_job_response(monkeypatch):
    unreal_socket_server = _load_unreal_socket_server(monkeypatch)

    assert unreal_socket_server._should_return_immediate_job_response({"type": "execute_python"}) is True
    assert unreal_socket_server._should_return_immediate_job_response({"type": "compile_blueprint"}) is True
    assert unreal_socket_server._should_return_immediate_job_response({"type": "get_editor_context"}) is False


def test_wait_for_completion_flag_overrides_default_job_response(monkeypatch):
    unreal_socket_server = _load_unreal_socket_server(monkeypatch)

    assert unreal_socket_server._should_return_immediate_job_response(
        {"type": "execute_python", "wait_for_completion": True}
    ) is False
    assert unreal_socket_server._should_return_immediate_job_response(
        {"type": "get_editor_context", "wait_for_completion": False}
    ) is True
