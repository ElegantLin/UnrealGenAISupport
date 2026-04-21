import json
import platform
import socket
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import unreal

from handlers import (
    actor_commands,
    anim_blueprint_commands,
    animation_commands,
    basic_commands,
    blueprint_commands,
    blueprint_inspect_commands,
    fab_commands,
    input_commands,
    plugin_commands,
    preflight_commands,
    python_commands,
    session_commands,
    transaction_commands,
    ui_commands,
)
from utils import logging as log

try:
    from utils.blueprint_graph import collect_supported_graph_types
    from utils.job_state import JobRecord, mark_cancelled, mark_completed, mark_failed, mark_running, to_dict
    from utils.mcp_response import API_VERSION
    from utils.safety import UNSAFE_COMMANDS
except ImportError:
    from Content.Python.utils.blueprint_graph import collect_supported_graph_types
    from Content.Python.utils.job_state import JobRecord, mark_cancelled, mark_completed, mark_failed, mark_running, to_dict
    from Content.Python.utils.mcp_response import API_VERSION
    from Content.Python.utils.safety import UNSAFE_COMMANDS


command_queue: List[str] = []
job_records: Dict[str, JobRecord] = {}
job_results: Dict[str, Dict[str, Any]] = {}
job_commands: Dict[str, Dict[str, Any]] = {}
state_lock = threading.Lock()

TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
DIRECT_THREAD_COMMANDS = {"handshake", "get_job_status", "cancel_job", "list_active_jobs"}
DEFAULT_WAIT_TIMEOUT_SECONDS = 30.0
COMMAND_WAIT_TIMEOUTS = {
    "add_nodes_bulk": 120.0,
    "compile_blueprint": 120.0,
    "connect_nodes_bulk": 120.0,
    "execute_python": 300.0,
    "execute_unreal_command": 300.0,
    "request_editor_restart": 300.0,
    "start_fab_add_to_project": 300.0,
    "start_fab_search": 120.0,
}

LONG_RUNNING_COMMANDS = {
    "add_nodes_bulk",
    "compile_blueprint",
    "connect_nodes_bulk",
    "execute_python",
    "execute_unreal_command",
    "request_editor_restart",
}


def _extract_warning_lines(recent_logs: List[str]) -> List[str]:
    warning_lines: List[str] = []
    seen = set()

    for line in recent_logs:
        if "warning" not in str(line).casefold():
            continue
        if line in seen:
            continue
        seen.add(line)
        warning_lines.append(line)

    return warning_lines


def _coerce_warning_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _merge_warnings(response: Dict[str, Any], recent_logs: List[str]) -> List[str]:
    warnings = []
    seen = set()

    for warning in _coerce_warning_list(response.get("warnings")) + _extract_warning_lines(recent_logs):
        if warning in seen:
            continue
        seen.add(warning)
        warnings.append(warning)

    return warnings


def _collect_recent_logs(start_line: Optional[int]) -> List[str]:
    end_snapshot = getattr(log, "end_command_log_snapshot", None)
    if start_line is None or not callable(end_snapshot):
        return []

    try:
        recent_logs = end_snapshot(start_line)
    except Exception as exc:
        log.log_warning(f"Failed to capture recent command logs: {exc}")
        return []

    if isinstance(recent_logs, list):
        return [str(line) for line in recent_logs]
    return []


def _job_pending_message(command_type: str, status: str, job_id: str) -> str:
    return (
        f"Command '{command_type}' is {status}. "
        f"Poll `get_job_status(job_id='{job_id}')` for completion."
    )


def _build_pending_job_response(job_id: str) -> Dict[str, Any]:
    with state_lock:
        record = job_records.get(job_id)

    if record is None:
        return {
            "success": False,
            "error": f"Unknown job_id: {job_id}",
            "error_code": "JOB_NOT_FOUND",
            "job_id": job_id,
        }

    return {
        "success": True,
        "message": _job_pending_message(record.command_type, record.status, record.job_id),
        "job_id": record.job_id,
        "command_type": record.command_type,
        "status": record.status,
        "progress": record.progress,
        "cancellable": record.cancellable,
        "result_available": False,
    }


def _build_job_not_found_response(job_id: str) -> Dict[str, Any]:
    return {
        "success": False,
        "error": f"Unknown job_id: {job_id}",
        "error_code": "JOB_NOT_FOUND",
        "job_id": job_id,
    }


def _build_terminal_job_response(record: JobRecord) -> Dict[str, Any]:
    payload = to_dict(record)
    payload["message"] = (
        f"Job '{record.job_id}' completed."
        if record.status == "completed"
        else f"Job '{record.job_id}' is {record.status}."
    )
    payload["success"] = record.status == "completed"

    if record.status != "completed":
        payload["error"] = record.error or payload.get("result", {}).get("error", f"Job {record.status}.")

    return payload


def _create_job(command: Dict[str, Any]) -> str:
    command_type = str(command.get("type", "")).strip()
    job_id = uuid.uuid4().hex
    record = JobRecord(
        job_id=job_id,
        command_type=command_type or "unknown",
        cancellable=command_type not in {"request_editor_restart"},
    )

    with state_lock:
        job_records[job_id] = record
        job_commands[job_id] = dict(command)
        command_queue.append(job_id)

    return job_id


def _resolve_wait_timeout(command: Dict[str, Any]) -> float:
    for key in ("socket_wait_timeout", "timeout_seconds"):
        value = command.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)

    return COMMAND_WAIT_TIMEOUTS.get(str(command.get("type", "")), DEFAULT_WAIT_TIMEOUT_SECONDS)


def _should_return_immediate_job_response(command: Dict[str, Any]) -> bool:
    if bool(command.get("async")):
        return True

    wait_for_completion = command.get("wait_for_completion")
    if wait_for_completion is False:
        return True
    if wait_for_completion is True:
        return False

    return str(command.get("type", "")).strip() in LONG_RUNNING_COMMANDS


def _wait_for_job_result(job_id: str, timeout_seconds: float) -> Dict[str, Any]:
    deadline = time.time() + max(timeout_seconds, 0.1)

    while time.time() < deadline:
        with state_lock:
            response = job_results.get(job_id)
            record = job_records.get(job_id)

        if response is not None:
            return response

        if record is not None and record.status in TERMINAL_JOB_STATUSES:
            return _build_terminal_job_response(record)

        time.sleep(0.05)

    return _build_pending_job_response(job_id)


def _cancel_queued_job(record: JobRecord) -> Dict[str, Any]:
    cancelled_record = mark_cancelled(record, message="Job was cancelled before execution.")
    cancelled_response = {
        "success": False,
        "message": "Job was cancelled before execution.",
        "error": "Job was cancelled before execution.",
        "error_code": "JOB_CANCELLED",
        "job_id": record.job_id,
        "command_type": record.command_type,
        "status": "cancelled",
        "progress": cancelled_record.progress,
        "cancellable": cancelled_record.cancellable,
        "recent_logs": [],
        "warnings": [],
    }

    with state_lock:
        if record.job_id in command_queue:
            command_queue.remove(record.job_id)
        job_records[record.job_id] = cancelled_record
        job_results[record.job_id] = cancelled_response

    return cancelled_response


class CommandDispatcher:
    """
    Dispatches commands to appropriate handlers based on command type.
    """

    def __init__(self):
        self.handlers = {
            "handshake": self._handle_handshake,
            "get_capabilities": self._handle_get_capabilities,
            "get_job_status": self._handle_get_job_status,
            "cancel_job": self._handle_cancel_job,
            "list_active_jobs": self._handle_list_active_jobs,

            # Basic object commands
            "spawn": basic_commands.handle_spawn,
            "create_material": basic_commands.handle_create_material,
            "modify_object": actor_commands.handle_modify_object,
            "take_screenshot": basic_commands.handle_take_screenshot,

            # Blueprint commands
            "create_blueprint": blueprint_commands.handle_create_blueprint,
            "add_component": blueprint_commands.handle_add_component,
            "add_variable": blueprint_commands.handle_add_variable,
            "add_function": blueprint_commands.handle_add_function,
            "add_node": blueprint_commands.handle_add_node,
            "connect_nodes": blueprint_commands.handle_connect_nodes,
            "compile_blueprint": blueprint_commands.handle_compile_blueprint,
            "spawn_blueprint": blueprint_commands.handle_spawn_blueprint,
            "delete_node": blueprint_commands.handle_delete_node,
            "get_node_guid": blueprint_commands.handle_get_node_guid,
            "get_all_nodes": blueprint_commands.handle_get_all_nodes,
            "get_node_suggestions": blueprint_commands.handle_get_node_suggestions,
            "add_nodes_bulk": blueprint_commands.handle_add_nodes_bulk,
            "connect_nodes_bulk": blueprint_commands.handle_connect_nodes_bulk,

            # Python and console
            "execute_python": python_commands.handle_execute_python,
            "execute_unreal_command": python_commands.handle_execute_unreal_command,

            # Plugin and restart management
            "get_editor_context": plugin_commands.handle_get_editor_context,
            "preflight_project": preflight_commands.handle_preflight_project,
            "set_plugin_enabled": plugin_commands.handle_set_plugin_enabled,
            "request_editor_restart": plugin_commands.handle_request_editor_restart,

            # Safe mutation runtime (P1)
            "preview_operation": transaction_commands.handle_preview_operation,
            "apply_operation": transaction_commands.handle_apply_operation,
            "undo_last_mcp_operation": transaction_commands.handle_undo_last_mcp_operation,

            # Blueprint inspection (P1.25)
            "get_graph_schema": blueprint_inspect_commands.handle_get_graph_schema,
            "resolve_graph_by_path": blueprint_inspect_commands.handle_resolve_graph_by_path,
            "get_graph_nodes": blueprint_inspect_commands.handle_get_graph_nodes,
            "get_graph_pins": blueprint_inspect_commands.handle_get_graph_pins,
            "resolve_node_by_selector": blueprint_inspect_commands.handle_resolve_node_by_selector,
            "get_pin_compatibility": blueprint_inspect_commands.handle_get_pin_compatibility,
            "suggest_autocast_path": blueprint_inspect_commands.handle_suggest_autocast_path,
            "compile_blueprint_with_diagnostics": blueprint_inspect_commands.handle_compile_blueprint_with_diagnostics,

            # Editor session restore (P1.5)
            "capture_editor_session": session_commands.handle_capture_editor_session,
            "restore_editor_session": session_commands.handle_restore_editor_session,
            "save_editor_session": session_commands.handle_save_editor_session,
            "open_asset": session_commands.handle_open_asset,
            "bring_asset_to_front": session_commands.handle_bring_asset_to_front,
            "focus_graph": session_commands.handle_focus_graph,
            "focus_node": session_commands.handle_focus_node,
            "select_actor": session_commands.handle_select_actor,

            # Enhanced Input (P2)
            "create_input_action": input_commands.handle_create_input_action,
            "create_input_mapping_context": input_commands.handle_create_input_mapping_context,
            "map_enhanced_input_action": input_commands.handle_map_enhanced_input_action,
            "list_input_mappings": input_commands.handle_list_input_mappings,
            "legacy_input_binding_warning": input_commands.handle_legacy_input_binding_warning,

            # BlendSpace (P3)
            "get_blend_space_info": animation_commands.handle_get_blend_space_info,
            "set_blend_space_axis": animation_commands.handle_set_blend_space_axis,
            "replace_blend_space_samples": animation_commands.handle_replace_blend_space_samples,
            "set_blend_space_sample_animation": animation_commands.handle_set_blend_space_sample_animation,

            # AnimBlueprint read (P4)
            "get_anim_blueprint_structure": anim_blueprint_commands.handle_get_anim_blueprint_structure,
            "get_graph_nodes": anim_blueprint_commands.handle_get_graph_nodes,
            "get_graph_pins": anim_blueprint_commands.handle_get_graph_pins,
            "resolve_graph_by_path": anim_blueprint_commands.handle_resolve_graph_by_path,

            # AnimBlueprint write (P5)
            "create_state_machine": anim_blueprint_commands.handle_create_state_machine,
            "create_state": anim_blueprint_commands.handle_create_state,
            "create_transition": anim_blueprint_commands.handle_create_transition,
            "set_transition_rule": anim_blueprint_commands.handle_set_transition_rule,
            "create_state_alias": anim_blueprint_commands.handle_create_state_alias,
            "set_alias_targets": anim_blueprint_commands.handle_set_alias_targets,
            "set_state_sequence_asset": anim_blueprint_commands.handle_set_state_sequence_asset,
            "set_state_blend_space_asset": anim_blueprint_commands.handle_set_state_blend_space_asset,
            "set_cached_pose_node": anim_blueprint_commands.handle_set_cached_pose_node,
            "set_default_slot_chain": anim_blueprint_commands.handle_set_default_slot_chain,
            "set_apply_additive_chain": anim_blueprint_commands.handle_set_apply_additive_chain,

            # Actor / component commands
            "edit_component_property": actor_commands.handle_edit_component_property,
            "add_component_with_events": actor_commands.handle_add_component_with_events,

            # Scene
            "get_all_scene_objects": basic_commands.handle_get_all_scene_objects,
            "create_project_folder": basic_commands.handle_create_project_folder,
            "get_files_in_folder": basic_commands.handle_get_files_in_folder,

            # Input
            "add_input_binding": basic_commands.handle_add_input_binding,

            # UI
            "add_widget_to_user_widget": ui_commands.handle_add_widget_to_user_widget,
            "edit_widget_property": ui_commands.handle_edit_widget_property,

            # Fab
            "start_fab_search": fab_commands.handle_start_fab_search,
            "start_fab_add_to_project": fab_commands.handle_start_fab_add_to_project,
            "get_fab_operation_status": fab_commands.handle_get_fab_operation_status,
        }

    def dispatch(self, command: Dict[str, Any]) -> Dict[str, Any]:
        command_type = command.get("type")
        if command_type not in self.handlers:
            return {"success": False, "error": f"Unknown command type: {command_type}"}

        try:
            handler = self.handlers[command_type]
            return handler(command)
        except Exception as exc:
            log.log_error(f"Error processing command: {exc}", include_traceback=True)
            return {"success": False, "error": str(exc)}

    def _handle_handshake(self, command: Dict[str, Any]) -> Dict[str, Any]:
        message = command.get("message", "")
        log.log_info(f"Handshake received: {message}")

        engine_version = unreal.SystemLibrary.get_engine_version()
        connection_info = {
            "status": "Connected",
            "engine_version": engine_version,
            "timestamp": time.time(),
            "session_id": f"UE-{int(time.time())}",
        }

        return {
            "success": True,
            "message": f"Received: {message}",
            "connection_info": connection_info,
        }

    def _handle_get_capabilities(self, command: Dict[str, Any]) -> Dict[str, Any]:
        del command

        editor_context = plugin_commands.handle_get_editor_context({})
        warnings = []
        if not editor_context.get("success"):
            warnings.append(editor_context.get("error", "Editor context is only partially available."))

        return {
            "success": True,
            "engine_version": unreal.SystemLibrary.get_engine_version(),
            "platform": platform.system(),
            "machine_architecture": platform.machine(),
            "input_system": editor_context.get("input_system", "unknown"),
            "supported_asset_types": [
                "Blueprint",
                "Material",
                "ProjectFolder",
                "UserWidget",
                "InputAction",
                "InputMappingContext",
                "BlendSpace",
                "BlendSpace1D",
            ],
            "supported_graph_types": collect_supported_graph_types(),
            "unsafe_commands": list(UNSAFE_COMMANDS),
            "supported_restore_policies": ["none", "assets_only", "assets_and_tabs"],
            "supported_input_value_types": ["Digital", "Axis1D", "Axis2D", "Axis3D"],
            "supported_blend_space_axis_kinds": ["Speed", "Direction", "Angle", "Custom"],
            "supported_anim_blueprint_features": [
                "state_machine",
                "state",
                "transition",
                "state_alias",
                "cached_pose",
                "default_slot",
                "apply_additive",
            ],
            "supported_transition_rule_kinds": ["always", "bool_property", "expression"],
            "supported_anim_play_modes": ["Loop", "Once", "Freeze"],
            "api_version": API_VERSION,
            "editor_context": editor_context,
            "warnings": warnings,
        }

    def _handle_get_job_status(self, command: Dict[str, Any]) -> Dict[str, Any]:
        job_id = str(command.get("job_id", "")).strip()
        if not job_id:
            return {
                "success": False,
                "error": "Missing required parameter: job_id",
                "error_code": "JOB_ID_REQUIRED",
            }

        with state_lock:
            record = job_records.get(job_id)

        if record is None:
            return _build_job_not_found_response(job_id)

        return _build_terminal_job_response(record) if record.status in TERMINAL_JOB_STATUSES else _build_pending_job_response(job_id)

    def _handle_cancel_job(self, command: Dict[str, Any]) -> Dict[str, Any]:
        job_id = str(command.get("job_id", "")).strip()
        if not job_id:
            return {
                "success": False,
                "error": "Missing required parameter: job_id",
                "error_code": "JOB_ID_REQUIRED",
            }

        with state_lock:
            record = job_records.get(job_id)

        if record is None:
            return _build_job_not_found_response(job_id)

        if record.status == "queued":
            return _cancel_queued_job(record)

        if record.status == "running":
            return {
                "success": False,
                "error": "Job is already running and cannot be cancelled safely.",
                "error_code": "JOB_NOT_CANCELLABLE",
                "job_id": job_id,
                "command_type": record.command_type,
                "status": record.status,
                "cancellable": False,
            }

        return _build_terminal_job_response(record)

    def _handle_list_active_jobs(self, command: Dict[str, Any]) -> Dict[str, Any]:
        del command

        with state_lock:
            active_jobs = [
                to_dict(record)
                for record in job_records.values()
                if record.status not in TERMINAL_JOB_STATUSES
            ]

        return {
            "success": True,
            "jobs": active_jobs,
            "count": len(active_jobs),
        }


dispatcher = CommandDispatcher()


def process_commands(delta_time=None):
    del delta_time

    with state_lock:
        if not command_queue:
            return

        job_id = command_queue.pop(0)
        command = job_commands.get(job_id)
        record = job_records.get(job_id)

    if not command or record is None:
        return

    if record.status == "cancelled":
        return

    log.log_info(f"Processing command on main thread for job {job_id}: {command}")

    begin_snapshot = getattr(log, "begin_command_log_snapshot", None)
    start_line = begin_snapshot() if callable(begin_snapshot) else None

    with state_lock:
        latest_record = job_records.get(job_id)
        if latest_record is None or latest_record.status == "cancelled":
            return
        job_records[job_id] = mark_running(latest_record, progress=0.05)

    try:
        response = dispatcher.dispatch(command)
        if not isinstance(response, dict):
            response = {
                "success": False,
                "error": f"Handler returned unexpected response type: {type(response).__name__}",
            }
    except Exception as exc:
        log.log_error(f"Error processing command: {exc}", include_traceback=True)
        response = {"success": False, "error": str(exc)}

    recent_logs = _collect_recent_logs(start_line)
    warnings = _merge_warnings(response, recent_logs)

    final_response = dict(response)
    final_response["job_id"] = job_id
    final_response["command_type"] = record.command_type
    final_response["recent_logs"] = recent_logs
    final_response["warnings"] = warnings

    with state_lock:
        latest_record = job_records.get(job_id)
        if latest_record is None:
            return
        if latest_record.status == "cancelled" and job_id in job_results:
            return

        if final_response.get("success"):
            job_records[job_id] = mark_completed(
                latest_record,
                result=final_response,
                recent_logs=recent_logs,
                warnings=warnings,
            )
        else:
            job_records[job_id] = mark_failed(
                latest_record,
                final_response.get("error", "Command failed."),
                result=final_response,
                recent_logs=recent_logs,
                warnings=warnings,
            )

        job_results[job_id] = final_response


def receive_all_data(conn, buffer_size=4096):
    data = b""
    while True:
        try:
            chunk = conn.recv(buffer_size)
            if not chunk:
                break

            data += chunk

            try:
                json.loads(data.decode("utf-8"))
                return data.decode("utf-8")
            except json.JSONDecodeError as json_err:
                if "Unterminated string" in str(json_err) or "Expecting" in str(json_err):
                    continue

                log.log_error(f"Malformed JSON received: {json_err}", include_traceback=True)
                return None

        except socket.timeout:
            log.log_warning("Socket timeout while receiving data")
            return data.decode("utf-8")
        except Exception as exc:
            log.log_error(f"Error receiving data: {exc}", include_traceback=True)
            return None

    return data.decode("utf-8")


def socket_server_thread():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("localhost", 9877))
    server_socket.listen(1)
    log.log_info("Unreal Engine socket server started on port 9877")

    while True:
        conn = None
        try:
            conn, _addr = server_socket.accept()
            conn.settimeout(5)

            data_str = receive_all_data(conn)

            if not data_str:
                conn.sendall(json.dumps({"success": False, "error": "No data received or error parsing data"}).encode())
                conn.close()
                continue

            try:
                command = json.loads(data_str)
            except json.JSONDecodeError as json_err:
                log.log_error(f"Error parsing JSON: {json_err}", include_traceback=True)
                conn.sendall(json.dumps({"success": False, "error": f"Invalid JSON: {json_err}"}).encode())
                conn.close()
                continue

            log.log_info(f"Received command: {command}")

            command_type = command.get("type")
            if command_type in DIRECT_THREAD_COMMANDS:
                response = dispatcher.dispatch(command)
                conn.sendall(json.dumps(response).encode())
                conn.close()
                continue

            job_id = _create_job(command)

            if _should_return_immediate_job_response(command):
                conn.sendall(json.dumps(_build_pending_job_response(job_id)).encode())
                conn.close()
                continue

            response = _wait_for_job_result(job_id, _resolve_wait_timeout(command))
            conn.sendall(json.dumps(response).encode())
            conn.close()
        except Exception as exc:
            log.log_error(f"Error in socket server: {exc}", include_traceback=True)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def register_command_processor():
    unreal.register_slate_post_tick_callback(process_commands)
    log.log_info("Command processor registered")


def initialize_server():
    thread = threading.Thread(target=socket_server_thread)
    thread.daemon = True
    thread.start()
    log.log_info("Socket server thread started")

    register_command_processor()

    log.log_info("Unreal Engine AI command server initialized successfully")
    log.log_info("Available commands:")
    log.log_info("  - Core: handshake, get_capabilities, get_editor_context, preflight_project")
    log.log_info("  - Jobs: get_job_status, cancel_job, list_active_jobs")
    log.log_info("  - Mutation: preview_operation, apply_operation, undo_last_mcp_operation")
    log.log_info("  - Blueprint: create_blueprint, add_component, add_variable, add_function, add_node, connect_nodes, compile_blueprint, spawn_blueprint, add_nodes_bulk, connect_nodes_bulk")
    log.log_info("  - Blueprint Inspection: get_graph_schema, resolve_graph_by_path, get_graph_nodes, get_graph_pins, resolve_node_by_selector, get_pin_compatibility, suggest_autocast_path, compile_blueprint_with_diagnostics")
    log.log_info("  - Basic: spawn, create_material, modify_object, take_screenshot")


initialize_server()
