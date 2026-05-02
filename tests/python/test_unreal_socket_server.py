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
        "transaction_commands": [
            "handle_preview_operation",
            "handle_apply_operation",
            "handle_undo_last_mcp_operation",
        ],
        "blueprint_inspect_commands": [
            "handle_get_graph_schema",
            "handle_resolve_graph_by_path",
            "handle_get_graph_nodes",
            "handle_get_graph_pins",
            "handle_resolve_node_by_selector",
            "handle_get_pin_compatibility",
            "handle_suggest_autocast_path",
            "handle_compile_blueprint_with_diagnostics",
        ],
        "session_commands": [
            "handle_capture_editor_session",
            "handle_restore_editor_session",
            "handle_save_editor_session",
            "handle_open_asset",
            "handle_bring_asset_to_front",
            "handle_focus_graph",
            "handle_focus_node",
            "handle_select_actor",
        ],
        "input_commands": [
            "handle_create_input_action",
            "handle_create_input_mapping_context",
            "handle_map_enhanced_input_action",
            "handle_list_input_mappings",
            "handle_legacy_input_binding_warning",
        ],
        "animation_commands": [
            "handle_get_blend_space_info",
            "handle_set_blend_space_axis",
            "handle_replace_blend_space_samples",
            "handle_set_blend_space_sample_animation",
        ],
        "actor_batch_commands": [
            "handle_duplicate_actors",
            "handle_replace_static_mesh",
            "handle_replace_material",
            "handle_group_actors",
            "handle_select_actors",
        ],
        "level_commands": [
            "handle_create_level_from_template",
            "handle_create_level_instance_from_selection",
            "handle_spawn_level_instance",
            "handle_add_level_to_world",
            "handle_list_level_instances",
        ],
        "landscape_commands": [
            "handle_create_landscape",
            "handle_set_landscape_material",
        ],
        "viewport_commands": [
            "handle_capture_editor_viewport",
        ],
        "project_settings_commands": [
            "handle_set_project_setting",
            "handle_set_rendering_defaults",
        ],
        "anim_blueprint_commands": [
            "handle_get_anim_blueprint_structure",
            "handle_get_graph_nodes",
            "handle_get_graph_pins",
            "handle_resolve_graph_by_path",
            "handle_create_state_machine",
            "handle_create_state",
            "handle_create_transition",
            "handle_set_transition_rule",
            "handle_create_state_alias",
            "handle_set_alias_targets",
            "handle_set_state_sequence_asset",
            "handle_set_state_blend_space_asset",
            "handle_set_cached_pose_node",
            "handle_set_default_slot_chain",
            "handle_set_apply_additive_chain",
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

    from Content.Python.utils import blueprint_graph as actual_blueprint_graph
    from Content.Python.utils import job_state as actual_job_state
    from Content.Python.utils import mcp_response as actual_mcp_response
    from Content.Python.utils import safety as actual_safety

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
    setattr(utils, "safety", actual_safety)
    setattr(utils, "blueprint_graph", actual_blueprint_graph)

    monkeypatch.setitem(sys.modules, "utils", utils)
    monkeypatch.setitem(sys.modules, "utils.logging", logging_module)
    monkeypatch.setitem(sys.modules, "utils.job_state", actual_job_state)
    monkeypatch.setitem(sys.modules, "utils.mcp_response", actual_mcp_response)
    monkeypatch.setitem(sys.modules, "utils.safety", actual_safety)
    monkeypatch.setitem(sys.modules, "utils.blueprint_graph", actual_blueprint_graph)

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


def test_get_capabilities_returns_required_fields(monkeypatch):
    unreal_socket_server = _load_unreal_socket_server(monkeypatch)
    dispatcher = unreal_socket_server.CommandDispatcher()

    monkeypatch.setattr(
        unreal_socket_server.plugin_commands,
        "handle_get_editor_context",
        lambda _command: {
            "success": True,
            "input_system": "EnhancedInput",
            "project_file_path": "/Projects/Test/Test.uproject",
        },
    )
    monkeypatch.setattr(unreal_socket_server.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(unreal_socket_server.platform, "machine", lambda: "arm64")

    payload = dispatcher._handle_get_capabilities({})

    assert payload["success"] is True
    assert payload["engine_version"] == "5.4"
    assert payload["api_version"] == unreal_socket_server.API_VERSION
    assert payload["platform"] == "Darwin"
    assert payload["machine_architecture"] == "arm64"
    assert payload["input_system"] == "EnhancedInput"
    assert set(payload["supported_asset_types"]) >= {
        "Blueprint",
        "Material",
        "ProjectFolder",
        "UserWidget",
        "InputAction",
        "InputMappingContext",
        "BlendSpace",
        "BlendSpace1D",
    }
    assert set(payload["supported_graph_types"]) >= {
        "UbergraphPages",
        "FunctionGraphs",
        "MacroGraphs",
        "AnimationGraphs",
        "AnimationStateMachineGraphs",
    }
    assert set(payload["unsafe_commands"]) >= {
        "execute_python",
        "execute_unreal_command",
        "request_editor_restart",
        "raw_anim_blueprint_node_edit",
    }
    assert payload["editor_context"]["project_file_path"] == "/Projects/Test/Test.uproject"
    assert payload["warnings"] == []


def test_dispatch_routes_get_capabilities_through_public_entry_point(monkeypatch):
    unreal_socket_server = _load_unreal_socket_server(monkeypatch)
    dispatcher = unreal_socket_server.CommandDispatcher()

    monkeypatch.setattr(
        unreal_socket_server.plugin_commands,
        "handle_get_editor_context",
        lambda _command: {
            "success": True,
            "input_system": "EnhancedInput",
            "project_file_path": "/Projects/Test/Test.uproject",
        },
    )
    monkeypatch.setattr(unreal_socket_server.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(unreal_socket_server.platform, "machine", lambda: "arm64")

    payload = dispatcher.dispatch({"type": "get_capabilities"})

    assert payload["success"] is True
    assert payload["engine_version"] == "5.4"
    assert payload["api_version"] == unreal_socket_server.API_VERSION
    assert payload["platform"] == "Darwin"
    assert payload["machine_architecture"] == "arm64"
    assert payload["input_system"] == "EnhancedInput"
    assert payload["editor_context"]["project_file_path"] == "/Projects/Test/Test.uproject"
    assert payload["warnings"] == []


def test_get_capabilities_returns_warning_when_editor_context_is_partial(monkeypatch):
    unreal_socket_server = _load_unreal_socket_server(monkeypatch)
    dispatcher = unreal_socket_server.CommandDispatcher()

    monkeypatch.setattr(
        unreal_socket_server.plugin_commands,
        "handle_get_editor_context",
        lambda _command: {
            "success": False,
            "error": "Editor context unavailable",
        },
    )
    monkeypatch.setattr(unreal_socket_server.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(unreal_socket_server.platform, "machine", lambda: "arm64")

    payload = dispatcher._handle_get_capabilities({})

    assert {
        "success",
        "engine_version",
        "platform",
        "machine_architecture",
        "input_system",
        "supported_asset_types",
        "supported_graph_types",
        "unsafe_commands",
        "api_version",
        "editor_context",
        "warnings",
    } <= payload.keys()
    assert payload["success"] is True
    assert payload["engine_version"] == "5.4"
    assert payload["api_version"] == unreal_socket_server.API_VERSION
    assert payload["platform"] == "Darwin"
    assert payload["machine_architecture"] == "arm64"
    assert payload["input_system"] == "unknown"
    assert set(payload["supported_asset_types"]) >= {
        "Blueprint",
        "Material",
        "ProjectFolder",
        "UserWidget",
        "InputAction",
        "InputMappingContext",
        "BlendSpace",
        "BlendSpace1D",
    }
    assert set(payload["supported_graph_types"]) >= {
        "UbergraphPages",
        "FunctionGraphs",
        "MacroGraphs",
        "AnimationGraphs",
        "AnimationStateMachineGraphs",
    }
    assert set(payload["unsafe_commands"]) >= {
        "execute_python",
        "execute_unreal_command",
        "request_editor_restart",
        "raw_anim_blueprint_node_edit",
    }
    assert payload["editor_context"] == {
        "success": False,
        "error": "Editor context unavailable",
    }
    assert payload["warnings"] == ["Editor context unavailable"]
