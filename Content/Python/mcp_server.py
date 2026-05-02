import socket
import json
import sys
import os
import subprocess
from fastmcp import FastMCP
import re
import time
from pathlib import Path

try:
    from utils.mcp_response import err, ok
except ImportError:
    from Content.Python.utils.mcp_response import err, ok


DEFAULT_UNREAL_HOST = "localhost"
DEFAULT_UNREAL_PORT = 9877
DEFAULT_SOCKET_TIMEOUT_SECONDS = 30.0
LONG_RUNNING_SOCKET_COMMANDS = {
    "add_nodes_bulk",
    "compile_blueprint",
    "connect_nodes_bulk",
    "execute_python",
    "execute_unreal_command",
    "request_editor_restart",
    "start_fab_add_to_project",
    "start_fab_search",
}


def get_unreal_host():
    return os.getenv("UNREAL_HOST", DEFAULT_UNREAL_HOST)


def get_unreal_port():
    port_str = os.getenv("UNREAL_PORT", str(DEFAULT_UNREAL_PORT))
    try:
        return int(port_str)
    except ValueError:
        print(f"Invalid UNREAL_PORT '{port_str}', falling back to {DEFAULT_UNREAL_PORT}", file=sys.stderr)
        return DEFAULT_UNREAL_PORT


# THIS FILE WILL RUN OUTSIDE THE UNREAL ENGINE SCOPE, 
# DO NOT IMPORT UNREAL MODULES HERE OR EXECUTE IT IN THE UNREAL ENGINE PYTHON INTERPRETER

# Create a PID file to let the Unreal plugin know this process is running
def write_pid_file():
    try:
        pid = os.getpid()
        pid_dir = os.path.join(os.path.expanduser("~"), ".unrealgenai")
        os.makedirs(pid_dir, exist_ok=True)
        pid_path = os.path.join(pid_dir, "mcp_server.pid")
        unreal_port = get_unreal_port()

        with open(pid_path, "w") as f:
            f.write(f"{pid}\n{unreal_port}")  # Store PID and port

        # Register to delete the PID file on exit
        import atexit
        def cleanup_pid_file():
            try:
                if os.path.exists(pid_path):
                    os.remove(pid_path)
            except:
                pass

        atexit.register(cleanup_pid_file)

        return pid_path
    except Exception as e:
        print(f"Failed to write PID file: {e}", file=sys.stderr)
        return None


# Write PID file on startup
pid_file = write_pid_file()
if pid_file:
    print(f"MCP Server started with PID file at: {pid_file}", file=sys.stderr)

# Create an MCP server
mcp = FastMCP("UnrealHandshake")


def _tool_success(message, data=None, **extra):
    return ok(message=message, data=data, **extra)


def _tool_error(message, error_code="TOOL_ERROR", **extra):
    return err(message=message, error_code=error_code, **extra)


def _response_warnings(result: dict) -> list:
    warnings = result.get("warnings")
    if isinstance(warnings, list):
        return [str(item) for item in warnings if str(item).strip()]
    if isinstance(warnings, str) and warnings.strip():
        return [warnings]
    return []


def _looks_like_structured_envelope(result: dict) -> bool:
    return (
        isinstance(result, dict)
        and "success" in result
        and "api_version" in result
        and ("data" in result or "error" in result)
    )


def _coerce_unreal_response(response, *, invalid_error_code: str = "INVALID_UNREAL_RESPONSE") -> dict:
    if isinstance(response, dict):
        return response

    if isinstance(response, str):
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError as exc:
            return {
                "success": False,
                "error": f"Failed to parse Unreal response: {exc}",
                "error_code": invalid_error_code,
                "raw_response": response,
            }

        if isinstance(parsed, dict):
            return parsed

        return {
            "success": False,
            "error": f"Unexpected Unreal response payload type: {type(parsed).__name__}",
            "error_code": invalid_error_code,
            "raw_response": response,
        }

    return {
        "success": False,
        "error": f"Unexpected Unreal response type: {type(response).__name__}",
        "error_code": invalid_error_code,
    }


def _tool_response_from_unreal(
    response,
    *,
    tool_name: str,
    success_message: str,
    error_code: str,
    default_error: str,
    extra_data=None,
):
    result = _coerce_unreal_response(response)
    data = {
        "tool": tool_name,
        "response": result,
    }
    if extra_data:
        data.update(extra_data(result))

    warnings = _response_warnings(result)
    if result.get("job_id") and result.get("status") in {"queued", "running"} and result.get("result_available") is False:
        return _tool_success(
            result.get("message", f"{tool_name} was accepted for background execution."),
            data=data,
            warnings=warnings,
            job_id=result.get("job_id"),
            status=result.get("status"),
            pending=True,
        )

    if result.get("success"):
        return _tool_success(
            success_message,
            data=data,
            warnings=warnings,
            job_id=result.get("job_id"),
            status=result.get("status"),
        )

    return _tool_error(
        result.get("error", default_error),
        error_code=result.get("error_code", error_code),
        data=data,
        warnings=warnings,
        job_id=result.get("job_id"),
        status=result.get("status"),
    )


def _command_tool_response(
    command: dict,
    *,
    tool_name: str,
    success_message: str,
    error_code: str,
    default_error: str,
    extra_data=None,
):
    return _tool_response_from_unreal(
        send_to_unreal(command),
        tool_name=tool_name,
        success_message=success_message,
        error_code=error_code,
        default_error=default_error,
        extra_data=extra_data,
    )


def _forward_structured_socket_response(
    response,
    *,
    success_message: str,
    error_code: str,
    default_error: str,
):
    result = _coerce_unreal_response(response)
    if _looks_like_structured_envelope(result):
        return result

    warnings = _response_warnings(result)
    if result.get("success"):
        return ok(success_message, data=result, warnings=warnings)
    return err(
        result.get("error", default_error),
        error_code=result.get("error_code", error_code),
        data=result,
        warnings=warnings,
    )


def _prepare_socket_command(command, timeout_seconds: float):
    if not isinstance(command, dict):
        return command, max(float(timeout_seconds), 1.0)

    prepared_command = dict(command)
    resolved_timeout = max(float(timeout_seconds), 1.0)

    command_timeout = prepared_command.get("timeout_seconds")
    if isinstance(command_timeout, (int, float)) and command_timeout > 0:
        resolved_timeout = max(resolved_timeout, float(command_timeout) + 5.0)

    if (
        prepared_command.get("type") in LONG_RUNNING_SOCKET_COMMANDS
        and "socket_wait_timeout" not in prepared_command
        and not prepared_command.get("async")
    ):
        prepared_command["socket_wait_timeout"] = max(5.0, min(resolved_timeout - 5.0, 25.0))

    socket_wait_timeout = prepared_command.get("socket_wait_timeout")
    if isinstance(socket_wait_timeout, (int, float)) and socket_wait_timeout > 0:
        resolved_timeout = max(resolved_timeout, float(socket_wait_timeout) + 5.0)

    return prepared_command, resolved_timeout


# Function to send a message to Unreal Engine via socket
def send_to_unreal(command, timeout_seconds: float = DEFAULT_SOCKET_TIMEOUT_SECONDS, suppress_errors: bool = False):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            prepared_command, socket_timeout = _prepare_socket_command(command, timeout_seconds)
            s.settimeout(socket_timeout)
            s.connect((get_unreal_host(), get_unreal_port()))

            # Ensure proper JSON encoding
            json_str = json.dumps(prepared_command)
            s.sendall(json_str.encode('utf-8'))

            # Implement robust response handling
            buffer_size = 8192  # Increased buffer size
            response_data = b""

            # Keep receiving data until we have complete JSON
            while True:
                chunk = s.recv(buffer_size)
                if not chunk:
                    break

                response_data += chunk

                # Check if we have complete JSON
                try:
                    json.loads(response_data.decode('utf-8'))
                    # If we get here, we have valid JSON
                    break
                except json.JSONDecodeError:
                    # Need more data, continue receiving
                    continue

            # Parse the complete response
            if response_data:
                return json.loads(response_data.decode('utf-8'))
            else:
                return {"success": False, "error": "No response received"}

        except Exception as e:
            if not suppress_errors:
                print(f"Error sending to Unreal: {e}", file=sys.stderr)
            return {"success": False, "error": str(e)}


def poll_fab_operation(operation_id: str, timeout_seconds: float, poll_interval: float = 0.5) -> dict:
    deadline = time.time() + max(timeout_seconds, 1.0)
    last_response = {"success": True, "status": "pending", "operation_id": operation_id}

    while time.time() < deadline:
        response = send_to_unreal({"type": "get_fab_operation_status", "operation_id": operation_id})
        if not isinstance(response, dict):
            return {"success": False, "status": "error", "error": "Invalid Fab operation status response"}

        last_response = response
        status = response.get("status")
        if status in ("completed", "error"):
            return response

        time.sleep(poll_interval)

    return {
        "success": False,
        "status": "error",
        "operation_id": operation_id,
        "error": f"Fab operation timed out after {timeout_seconds} seconds"
    }


def _spawn_detached_process(command: list) -> subprocess.Popen:
    popen_kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }

    if os.name == "nt":
        detached_process = 0x00000008
        create_new_process_group = 0x00000200
        popen_kwargs["creationflags"] = detached_process | create_new_process_group
    else:
        popen_kwargs["start_new_session"] = True

    return subprocess.Popen(command, **popen_kwargs)


def _launch_restart_launcher(editor_path: str, project_file_path: str, timeout_seconds: float) -> dict:
    launcher_path = Path(__file__).parent / "restart_editor_launcher.py"
    if not launcher_path.exists():
        return {"success": False, "error": f"Restart launcher not found at {launcher_path}"}

    command = [
        sys.executable,
        str(launcher_path),
        "--editor-path", editor_path,
        "--project-file-path", project_file_path,
        "--host", get_unreal_host(),
        "--port", str(get_unreal_port()),
        "--wait-for-port-close-timeout", str(max(15.0, min(timeout_seconds, 60.0))),
    ]

    try:
        process = _spawn_detached_process(command)
        return {"success": True, "launcher_pid": process.pid, "launcher_path": str(launcher_path)}
    except Exception as e:
        return {"success": False, "error": f"Failed to start restart launcher: {str(e)}"}


def _wait_for_editor_reconnect(previous_editor_pid: int, timeout_seconds: float, poll_interval: float = 1.0) -> dict:
    deadline = time.time() + max(timeout_seconds, 1.0)
    saw_disconnect = False
    last_error = "Timed out waiting for Unreal Editor to reconnect."

    while time.time() < deadline:
        response = send_to_unreal(
            {"type": "get_editor_context"},
            timeout_seconds=min(1.0, timeout_seconds),
            suppress_errors=True,
        )

        if response.get("success"):
            current_editor_pid = response.get("editor_pid")
            if previous_editor_pid and current_editor_pid and current_editor_pid != previous_editor_pid:
                return response
            if previous_editor_pid in (None, 0) and saw_disconnect:
                return response
        else:
            saw_disconnect = True
            last_error = response.get("error", last_error)

        time.sleep(poll_interval)

    return {"success": False, "error": last_error}


def _format_dirty_packages(dirty_packages, limit: int = 5) -> str:
    if not dirty_packages:
        return ""

    preview = ", ".join(dirty_packages[:limit])
    if len(dirty_packages) > limit:
        preview += f", and {len(dirty_packages) - limit} more"
    return preview


def _build_restart_confirmation_result(dirty_packages) -> dict:
    return {
        "success": False,
        "confirmation_required": True,
        "reason": "unsaved_changes",
        "error": (
            "Unreal Editor has unsaved assets or maps. Ask the user to confirm whether to restart without saving, "
            "then retry with force=True."
        ),
        "dirty_packages": dirty_packages,
        "dirty_package_count": len(dirty_packages),
        "suggested_retry": "restart_editor(force=True)",
    }


def _format_restart_failure(result: dict) -> str:
    dirty_packages = _format_dirty_packages(result.get("dirty_packages", []))
    suffix = f" Dirty packages: {dirty_packages}." if dirty_packages else ""

    if result.get("confirmation_required"):
        suggested_retry = result.get("suggested_retry", "restart_editor(force=True)")
        return (
            f"Confirmation required before restart: {result.get('error', 'Unreal has unsaved changes.')}"
            f" Ask the user whether to continue without saving, then retry with `{suggested_retry}`.{suffix}"
        )

    return f"Failed to restart editor: {result.get('error', 'Unknown error')}.{suffix}"


def _restart_editor_impl(force: bool, wait_for_reconnect: bool, timeout_seconds: float, editor_path: str = "") -> dict:
    context = send_to_unreal({"type": "get_editor_context"})
    if not context.get("success"):
        return context

    project_file_path = context.get("project_file_path", "")
    if not project_file_path:
        return {"success": False, "error": "Unreal did not report a valid .uproject path."}

    dirty_packages = context.get("dirty_packages", [])
    if dirty_packages and not force:
        return _build_restart_confirmation_result(dirty_packages)

    resolved_editor_path = editor_path.strip() if editor_path else str(context.get("editor_path", "")).strip()
    if not resolved_editor_path:
        candidates = context.get("editor_path_candidates", [])
        candidate_suffix = f" Candidates: {candidates}" if candidates else ""
        return {
            "success": False,
            "error": f"Unable to resolve the Unreal Editor executable path.{candidate_suffix}",
        }

    launcher_result = _launch_restart_launcher(resolved_editor_path, project_file_path, timeout_seconds)
    if not launcher_result.get("success"):
        return launcher_result

    restart_response = send_to_unreal({
        "type": "request_editor_restart",
        "force": force,
        "delay_seconds": 1.5,
    })
    if not restart_response.get("success"):
        restart_response["launcher_pid"] = launcher_result.get("launcher_pid")
        return restart_response

    result = {
        "success": True,
        "project_file_path": project_file_path,
        "editor_path": resolved_editor_path,
        "launcher_pid": launcher_result.get("launcher_pid"),
        "message": restart_response.get("message", "Editor restart scheduled."),
    }

    if wait_for_reconnect:
        reconnect_response = _wait_for_editor_reconnect(context.get("editor_pid"), timeout_seconds)
        if reconnect_response.get("success"):
            result["reconnected"] = True
            result["new_editor_pid"] = reconnect_response.get("editor_pid")
            result["message"] = "Editor restarted and reconnected successfully."
        else:
            result["success"] = False
            result["reconnected"] = False
            result["error"] = reconnect_response.get("error", "Timed out waiting for Unreal to reconnect.")
    else:
        result["reconnected"] = False
        result["message"] = "Editor restart scheduled. Reconnect polling was skipped."

    return result


@mcp.tool()
def how_to_use() -> dict:
    """Load the LLM-oriented MCP usage guide as a structured response envelope."""
    try:
        current_dir = Path(__file__).parent
        md_file_path = current_dir / "knowledge_base" / "how_to_use.md"

        if not md_file_path.exists():
            return _tool_error(
                "how_to_use.md not found in knowledge_base subfolder.",
                error_code="HOW_TO_USE_NOT_FOUND",
                data={
                    "tool": "how_to_use",
                    "path": str(md_file_path),
                },
            )

        with open(md_file_path, "r", encoding="utf-8") as md_file:
            return _tool_success(
                "Loaded usage guide.",
                data={
                    "tool": "how_to_use",
                    "path": str(md_file_path),
                    "content": md_file.read(),
                },
            )

    except Exception as e:
        return _tool_error(
            f"Error loading how_to_use.md: {str(e)}",
            error_code="HOW_TO_USE_LOAD_FAILED",
            data={
                "tool": "how_to_use",
            },
        )


# Define basic tools for Claude to call

@mcp.tool()
def handshake_test(message: str) -> dict:
    """Send a handshake message to Unreal Engine and return a structured response envelope."""
    try:
        return _command_tool_response(
            {
                "type": "handshake",
                "message": message,
            },
            tool_name="handshake_test",
            success_message="Handshake successful.",
            error_code="HANDSHAKE_FAILED",
            default_error="Handshake failed.",
            extra_data=lambda result: {
                "message": message,
                "connection_info": result.get("connection_info", {}),
            },
        )
    except Exception as e:
        return _tool_error(
            f"Error communicating with Unreal: {str(e)}",
            error_code="HANDSHAKE_FAILED",
            data={
                "tool": "handshake_test",
                "message": message,
            },
        )


@mcp.tool()
def execute_python_script(script: str) -> dict:
    """
    Execute a Python script within Unreal Engine's Python interpreter.
    
    Args:
        script: A string containing the Python code to execute in Unreal Engine.
        
    Returns:
        Structured response envelope dict. Success payloads include `success=True`,
        a human-readable `message`, and `data` containing the tool name, script,
        command output, and raw Unreal response. Failure or confirmation payloads
        include `success=False`, `error`, `error_code`, and any relevant metadata.
        
    Note:
        This tool sends the script to Unreal Engine, where it is executed via a temporary file using Unreal's internal
        Python execution system (similar to GEngine->Exec). This method is stable but may not handle Blueprint-specific
        APIs as seamlessly as direct Python API calls. For Blueprint manipulation, consider using dedicated tools like
        `add_node_to_blueprint` or ensuring the script uses stable `unreal` module functions. Use this tool for Python
        script execution instead of `execute_unreal_command` with 'py' commands.
    """
    try:
        if is_potentially_destructive(script):
            return _tool_error(
                "This script appears to involve potentially destructive actions (e.g., deleting or saving files) that were not explicitly requested. Please confirm if you want to proceed by saying 'Yes, execute it' or modify your request to explicitly allow such actions.",
                error_code="CONFIRMATION_REQUIRED",
                confirmation_required=True,
                data={
                    "tool": "execute_python_script",
                    "script": script,
                },
            )

        command = {
            "type": "execute_python",
            "script": script
        }
        response = send_to_unreal(command)
        return _tool_response_from_unreal(
            response,
            tool_name="execute_python_script",
            success_message="Script executed successfully.",
            error_code="EXECUTE_PYTHON_FAILED",
            default_error="Failed to execute script.",
            extra_data=lambda result: {
                "script": script,
                "output": result.get("output", ""),
            },
        )
    except Exception as e:
        return _tool_error(
            f"Error sending script to Unreal: {str(e)}",
            error_code="EXECUTE_PYTHON_FAILED",
            data={
                "tool": "execute_python_script",
                "script": script,
            },
        )


@mcp.tool()
def execute_unreal_command(command: str) -> dict:
    """
    Execute an Unreal Engine command-line (CMD) command.
    
    Args:
        command: A string containing the Unreal Engine command to execute (e.g., "obj list", "stat fps").
        
    Returns:
        Structured response envelope dict. Success payloads include `success=True`,
        a human-readable `message`, and `data` containing the tool name, command,
        command output, and raw Unreal response. Failure or confirmation payloads
        include `success=False`, `error`, `error_code`, and any relevant metadata.
        
    Note:
        This tool executes commands directly in Unreal Engine's command system, similar to the editor's console.
        It is intended for built-in editor commands (e.g., "stat fps", "obj list") and not for running Python scripts.
        Do not use this tool with 'py' commands (e.g., "py script.py"); instead, use `execute_python_script` for Python
        execution, which provides dedicated safety checks and output handling. Output capture is limited; for detailed
        output, consider wrapping the command in a Python script with `execute_python_script`.
    """
    try:
        # Check if the command is attempting to run a Python script
        if command.strip().lower().startswith("py "):
            return _tool_error(
                "Use `execute_python_script` to run Python scripts instead of `execute_unreal_command` with 'py' commands. For example, use `execute_python_script(script='your_code_here')` for Python execution.",
                error_code="INVALID_COMMAND",
                data={
                    "tool": "execute_unreal_command",
                    "command": command,
                },
            )

        # Check for potentially destructive commands
        destructive_keywords = ["delete", "save", "quit", "exit", "restart"]
        if any(keyword in command.lower() for keyword in destructive_keywords):
            return _tool_error(
                "This command appears to involve potentially destructive actions (e.g., deleting or saving). Please confirm by saying 'Yes, execute it' or explicitly request such actions.",
                error_code="CONFIRMATION_REQUIRED",
                confirmation_required=True,
                data={
                    "tool": "execute_unreal_command",
                    "command": command,
                },
            )

        command_dict = {
            "type": "execute_unreal_command",
            "command": command
        }
        response = send_to_unreal(command_dict)
        return _tool_response_from_unreal(
            response,
            tool_name="execute_unreal_command",
            success_message="Command executed successfully.",
            error_code="EXECUTE_UNREAL_COMMAND_FAILED",
            default_error="Failed to execute Unreal command.",
            extra_data=lambda result: {
                "command": command,
                "output": result.get("output", ""),
            },
        )
    except Exception as e:
        return _tool_error(
            f"Error sending command to Unreal: {str(e)}",
            error_code="EXECUTE_UNREAL_COMMAND_FAILED",
            data={
                "tool": "execute_unreal_command",
                "command": command,
            },
        )


#
# Basic Object Commands
#

@mcp.tool()
def spawn_object(actor_class: str, location: list = [0, 0, 0], rotation: list = [0, 0, 0],
                 scale: list = [1, 1, 1], actor_label: str = None) -> dict:
    """
    Spawn an object in the Unreal Engine level
    
    Args:
        actor_class: For basic shapes, use: "Cube", "Sphere", "Cylinder", or "Cone".
                     For other actors, use class name like "PointLight" or full path.
        location: [X, Y, Z] coordinates
        rotation: [Pitch, Yaw, Roll] in degrees
        scale: [X, Y, Z] scale factors
        actor_label: Optional custom name for the actor
        
    Returns:
        Message indicating success or failure
    """
    command = {
        "type": "spawn",
        "actor_class": actor_class,
        "location": location,
        "rotation": rotation,
        "scale": scale,
        "actor_label": actor_label
    }

    response = _coerce_unreal_response(send_to_unreal(command))
    if not response.get("success"):
        error = response.get("error", "Unknown error")
        if "not found" in error:
            error += (
                "\nHint: For basic shapes, use 'Cube', 'Sphere', 'Cylinder', or 'Cone'. "
                "For other actors, try using '/Script/Engine.PointLight' format."
            )
            response["error"] = error

    return _tool_response_from_unreal(
        response,
        tool_name="spawn_object",
        success_message="Object spawned successfully.",
        error_code="SPAWN_OBJECT_FAILED",
        default_error="Failed to spawn object.",
        extra_data=lambda result: {
            "actor_class": actor_class,
            "location": location,
            "rotation": rotation,
            "scale": scale,
            "actor_label": actor_label,
            "actor_name": result.get("actor_name"),
        },
    )


@mcp.tool()
def edit_component_property(blueprint_path: str, component_name: str, property_name: str, value: str,
                            is_scene_actor: bool = False, actor_name: str = "") -> dict:
    """
    Edit a property of a component in a Blueprint or scene actor.

    Args:
        blueprint_path: Path to the Blueprint (e.g., "/Game/FlappyBird/BP_FlappyBird") or "" for scene actors
        component_name: Name of the component (e.g., "BirdMesh", "RootComponent")
        property_name: Name of the property to edit (e.g., "StaticMesh", "RelativeLocation")
        value: New value as a string (e.g., "'/Engine/BasicShapes/Sphere.Sphere'", "100,200,300")
        is_scene_actor: If True, edit a component on a scene actor (default: False)
        actor_name: Name of the actor in the scene (required if is_scene_actor is True, e.g., "Cube_1")

    Returns:
        Message indicating success or failure, with optional property suggestions if the property is not found.

    Capabilities:
        - Set component properties in Blueprints (e.g., StaticMesh, bSimulatePhysics).
        - Modify scene actor components (e.g., position, rotation, scale, material).
        - Supports scalar types (float, int, bool), objects (e.g., materials), and vectors/rotators (e.g., "100,200,300" for FVector).
        - Examples:
            - Set a mesh: edit_component_property("/Game/FlappyBird/BP_FlappyBird", "BirdMesh", "StaticMesh", "'/Engine/BasicShapes/Sphere.Sphere'")
            - Move an actor: edit_component_property("", "RootComponent", "RelativeLocation", "100,200,300", True, "Cube_1")
            - Rotate an actor: edit_component_property("", "RootComponent", "RelativeRotation", "0,90,0", True, "Cube_1")
            - Scale an actor: edit_component_property("", "RootComponent", "RelativeScale3D", "2,2,2", True, "Cube_1")
            - Enable physics: edit_component_property("/Game/FlappyBird/BP_FlappyBird", "BirdMesh", "bSimulatePhysics", "true")
    """
    command = {
        "type": "edit_component_property",
        "blueprint_path": blueprint_path,
        "component_name": component_name,
        "property_name": property_name,
        "value": value,
        "is_scene_actor": is_scene_actor,
        "actor_name": actor_name
    }
    return _tool_response_from_unreal(
        send_to_unreal(command),
        tool_name="edit_component_property",
        success_message=f"Updated property '{property_name}' on '{component_name}'.",
        error_code="EDIT_COMPONENT_PROPERTY_FAILED",
        default_error="Failed to edit component property.",
        extra_data=lambda result: {
            "blueprint_path": blueprint_path,
            "component_name": component_name,
            "property_name": property_name,
            "value": value,
            "is_scene_actor": is_scene_actor,
            "actor_name": actor_name,
            "suggestions": result.get("suggestions"),
        },
    )


@mcp.tool()
def create_material(material_name: str, color: list) -> dict:
    """
    Create a new material with the specified color
    
    Args:
        material_name: Name for the new material
        color: [R, G, B] color values (0-1)
        
    Returns:
        Message indicating success or failure, and the material path if successful
    """
    command = {
        "type": "create_material",
        "material_name": material_name,
        "color": color
    }

    return _command_tool_response(
        command,
        tool_name="create_material",
        success_message="Material created successfully.",
        error_code="CREATE_MATERIAL_FAILED",
        default_error="Failed to create material.",
        extra_data=lambda result: {
            "material_name": material_name,
            "color": color,
            "material_path": result.get("material_path"),
        },
    )


#
# Blueprint Commands
#

@mcp.tool()
def create_blueprint(blueprint_name: str, parent_class: str = "Actor", save_path: str = "/Game/Blueprints") -> dict:
    """
    Create a new Blueprint class
    
    Args:
        blueprint_name: Name for the new Blueprint
        parent_class: Parent class name or path (e.g., "Actor", "/Script/Engine.Actor")
        save_path: Path to save the Blueprint asset
        
    Returns:
        Message indicating success or failure
    """
    command = {
        "type": "create_blueprint",
        "blueprint_name": blueprint_name,
        "parent_class": parent_class,
        "save_path": save_path
    }

    return _command_tool_response(
        command,
        tool_name="create_blueprint",
        success_message="Blueprint created successfully.",
        error_code="CREATE_BLUEPRINT_FAILED",
        default_error="Failed to create Blueprint.",
        extra_data=lambda result: {
            "blueprint_name": blueprint_name,
            "parent_class": parent_class,
            "save_path": save_path,
            "blueprint_path": result.get("blueprint_path", f"{save_path}/{blueprint_name}"),
        },
    )

@mcp.tool()
def take_editor_screenshot() -> dict:
    """
    Deprecated compatibility wrapper for editor viewport capture.

    This intentionally delegates to ``capture_editor_viewport`` so the tool
    captures Unreal's viewport rather than the user's full desktop.
    """
    response = capture_editor_viewport()
    warnings = _response_warnings(response)
    warnings.append("take_editor_screenshot is deprecated; use capture_editor_viewport.")
    response["warnings"] = warnings
    return response


@mcp.tool()
def add_component_to_blueprint(blueprint_path: str, component_class: str, component_name: str = None) -> dict:
    """
    Add a component to a Blueprint
    
    Args:
        blueprint_path: Path to the Blueprint asset
        component_class: Component class to add (e.g., "StaticMeshComponent", "PointLightComponent")
        component_name: Name for the new component (optional)
        
    Returns:
        Message indicating success or failure
    """
    command = {
        "type": "add_component",
        "blueprint_path": blueprint_path,
        "component_class": component_class,
        "component_name": component_name
    }

    return _command_tool_response(
        command,
        tool_name="add_component_to_blueprint",
        success_message="Component added to Blueprint successfully.",
        error_code="ADD_COMPONENT_FAILED",
        default_error="Failed to add component to Blueprint.",
        extra_data=lambda result: {
            "blueprint_path": blueprint_path,
            "component_class": component_class,
            "component_name": component_name,
        },
    )


@mcp.tool()
def add_variable_to_blueprint(blueprint_path: str, variable_name: str, variable_type: str,
                              default_value: str = None, category: str = "Default") -> dict:
    """
    Add a variable to a Blueprint
    
    Args:
        blueprint_path: Path to the Blueprint asset
        variable_name: Name for the new variable
        variable_type: Type of the variable (e.g., "float", "vector", "boolean")
        default_value: Default value for the variable (optional)
        category: Category for organizing variables in the Blueprint editor (optional)
        
    Returns:
        Message indicating success or failure
    """
    # Convert default_value to string if it's a number
    if default_value is not None and not isinstance(default_value, str):
        default_value = str(default_value)

    command = {
        "type": "add_variable",
        "blueprint_path": blueprint_path,
        "variable_name": variable_name,
        "variable_type": variable_type,
        "default_value": default_value,
        "category": category
    }

    return _command_tool_response(
        command,
        tool_name="add_variable_to_blueprint",
        success_message="Blueprint variable added successfully.",
        error_code="ADD_VARIABLE_FAILED",
        default_error="Failed to add variable to Blueprint.",
        extra_data=lambda result: {
            "blueprint_path": blueprint_path,
            "variable_name": variable_name,
            "variable_type": variable_type,
            "default_value": default_value,
            "category": category,
        },
    )


@mcp.tool()
def add_function_to_blueprint(blueprint_path: str, function_name: str,
                              inputs: list = None, outputs: list = None) -> dict:
    """
    Add a function to a Blueprint
    
    Args:
        blueprint_path: Path to the Blueprint asset
        function_name: Name for the new function
        inputs: List of input parameters [{"name": "param1", "type": "float"}, ...]
        outputs: List of output parameters [{"name": "return", "type": "boolean"}, ...]
        
    Returns:
        Message indicating success or failure
    """
    if inputs is None:
        inputs = []
    if outputs is None:
        outputs = []

    command = {
        "type": "add_function",
        "blueprint_path": blueprint_path,
        "function_name": function_name,
        "inputs": inputs,
        "outputs": outputs
    }

    return _command_tool_response(
        command,
        tool_name="add_function_to_blueprint",
        success_message="Blueprint function added successfully.",
        error_code="ADD_FUNCTION_FAILED",
        default_error="Failed to add function to Blueprint.",
        extra_data=lambda result: {
            "blueprint_path": blueprint_path,
            "function_name": function_name,
            "inputs": inputs,
            "outputs": outputs,
            "function_id": result.get("function_id"),
        },
    )


@mcp.tool()
def add_node_to_blueprint(blueprint_path: str, function_id: str, node_type: str,
                          node_position: list = [0, 0], node_properties: dict = None) -> dict:
    """
    Add a node to a Blueprint graph
    
    Args:
        blueprint_path: Path to the Blueprint asset
        function_id: ID of the function to add the node to
        node_type: Type of node to add. Common supported types include:
            - Basic nodes: "ReturnNode", "FunctionEntry", "Branch", "Sequence"
            - Math operations: "Multiply", "Add", "Subtract", "Divide"
            - Utilities: "PrintString", "Delay", "GetActorLocation", "SetActorLocation"
            - For other functions, try using the exact function name from Blueprints
              (e.g., "GetWorldLocation", "SpawnActorFromClass")
            If the requested node type isn't found, the system will search for alternatives
            and return suggestions. You can then use these suggestions in a new request.
        node_position: Position of the node in the graph [X, Y]. **IMPORTANT**: Space nodes
            at least 400 units apart horizontally and 300 units vertically to avoid overlap
            and ensure a clean, organized graph (e.g., [0, 0], [400, 0], [800, 0] for a chain).
        node_properties: Properties to set on the node (optional)
    
    Returns:
        On success: The node ID (GUID)
        On failure: A response containing "SUGGESTIONS:" followed by alternative node types to try
    
    Note:
        Function libraries like KismetMathLibrary, KismetSystemLibrary, and KismetStringLibrary 
        contain most common Blueprint functions. If a simple node name doesn't work, try the 
        full function name, e.g., "Multiply_FloatFloat" instead of just "Multiply".
    """
    if node_properties is None:
        node_properties = {}

    command = {
        "type": "add_node",
        "blueprint_path": blueprint_path,
        "function_id": function_id,
        "node_type": node_type,
        "node_position": node_position,
        "node_properties": node_properties
    }

    return _command_tool_response(
        command,
        tool_name="add_node_to_blueprint",
        success_message="Blueprint node added successfully.",
        error_code="ADD_NODE_FAILED",
        default_error="Failed to add node to Blueprint.",
        extra_data=lambda result: {
            "blueprint_path": blueprint_path,
            "function_id": function_id,
            "node_type": node_type,
            "node_position": node_position,
            "node_properties": node_properties,
            "node_id": result.get("node_id"),
        },
    )


@mcp.tool()
def get_node_suggestions(node_type: str) -> dict:
    """
    Get suggestions for a node type in Unreal Blueprints
    
    Args:
        node_type: The partial or full node type to get suggestions for (e.g., "Add", "FloatToDouble")
        
    Returns:
        A string indicating success with suggestions or an error message
    """
    command = {
        "type": "get_node_suggestions",
        "node_type": node_type
    }

    return _command_tool_response(
        command,
        tool_name="get_node_suggestions",
        success_message="Node suggestions loaded.",
        error_code="GET_NODE_SUGGESTIONS_FAILED",
        default_error="Failed to get node suggestions.",
        extra_data=lambda result: {
            "node_type": node_type,
            "suggestions": result.get("suggestions", []),
        },
    )


@mcp.tool()
def delete_node_from_blueprint(blueprint_path: str, function_id: str, node_id: str) -> dict:
    """
    Delete a node from a Blueprint graph
    
    Args:
        blueprint_path: Path to the Blueprint asset
        function_id: ID of the function containing the node
        node_id: ID of the node to delete
        
    Returns:
        Success or failure message
    """
    command = {
        "type": "delete_node",
        "blueprint_path": blueprint_path,
        "function_id": function_id,
        "node_id": node_id
    }

    return _command_tool_response(
        command,
        tool_name="delete_node_from_blueprint",
        success_message="Blueprint node deleted successfully.",
        error_code="DELETE_NODE_FAILED",
        default_error="Failed to delete node from Blueprint.",
        extra_data=lambda result: {
            "blueprint_path": blueprint_path,
            "function_id": function_id,
            "node_id": node_id,
        },
    )


@mcp.tool()
def get_all_nodes_in_graph(blueprint_path: str, function_id: str) -> dict:
    """
    Get all nodes in a Blueprint graph with their positions and types
    
    Args:
        blueprint_path: Path to the Blueprint asset
        function_id: ID of the function to get nodes from
        
    Returns:
        JSON string containing all nodes with their GUIDs, types, and positions
    """
    command = {
        "type": "get_all_nodes",
        "blueprint_path": blueprint_path,
        "function_id": function_id
    }

    return _command_tool_response(
        command,
        tool_name="get_all_nodes_in_graph",
        success_message="Graph nodes loaded.",
        error_code="GET_ALL_NODES_FAILED",
        default_error="Failed to get nodes from Blueprint graph.",
        extra_data=lambda result: {
            "blueprint_path": blueprint_path,
            "function_id": function_id,
            "nodes": result.get("nodes", []),
        },
    )


@mcp.tool()
def connect_blueprint_nodes(blueprint_path: str, function_id: str,
                            source_node_id: str, source_pin: str,
                            target_node_id: str, target_pin: str) -> dict:
    command = {
        "type": "connect_nodes",
        "blueprint_path": blueprint_path,
        "function_id": function_id,
        "source_node_id": source_node_id,
        "source_pin": source_pin,
        "target_node_id": target_node_id,
        "target_pin": target_pin
    }

    return _command_tool_response(
        command,
        tool_name="connect_blueprint_nodes",
        success_message="Blueprint nodes connected successfully.",
        error_code="CONNECT_NODES_FAILED",
        default_error="Failed to connect Blueprint nodes.",
        extra_data=lambda result: {
            "blueprint_path": blueprint_path,
            "function_id": function_id,
            "source_node_id": source_node_id,
            "source_pin": source_pin,
            "target_node_id": target_node_id,
            "target_pin": target_pin,
            "source_available_pins": result.get("source_available_pins", []),
            "target_available_pins": result.get("target_available_pins", []),
            "source_pin_details": result.get("source_pin"),
            "target_pin_details": result.get("target_pin"),
        },
    )


@mcp.tool()
def compile_blueprint(blueprint_path: str) -> dict:
    """
    Compile a Blueprint
    
    Args:
        blueprint_path: Path to the Blueprint asset
        
    Returns:
        Message indicating success or failure
    """
    command = {
        "type": "compile_blueprint",
        "blueprint_path": blueprint_path
    }

    return _command_tool_response(
        command,
        tool_name="compile_blueprint",
        success_message="Blueprint compiled successfully.",
        error_code="COMPILE_BLUEPRINT_FAILED",
        default_error="Failed to compile Blueprint.",
        extra_data=lambda result: {
            "blueprint_path": blueprint_path,
        },
    )


@mcp.tool()
def spawn_blueprint_actor(blueprint_path: str, location: list = [0, 0, 0],
                          rotation: list = [0, 0, 0], scale: list = [1, 1, 1],
                          actor_label: str = None) -> dict:
    """
    Spawn a Blueprint actor in the level
    
    Args:
        blueprint_path: Path to the Blueprint asset
        location: [X, Y, Z] coordinates
        rotation: [Pitch, Yaw, Roll] in degrees
        scale: [X, Y, Z] scale factors
        actor_label: Optional custom name for the actor
        
    Returns:
        Message indicating success or failure
    """
    command = {
        "type": "spawn_blueprint",
        "blueprint_path": blueprint_path,
        "location": location,
        "rotation": rotation,
        "scale": scale,
        "actor_label": actor_label
    }

    return _command_tool_response(
        command,
        tool_name="spawn_blueprint_actor",
        success_message="Blueprint actor spawned successfully.",
        error_code="SPAWN_BLUEPRINT_FAILED",
        default_error="Failed to spawn Blueprint actor.",
        extra_data=lambda result: {
            "blueprint_path": blueprint_path,
            "location": location,
            "rotation": rotation,
            "scale": scale,
            "actor_label": actor_label,
            "actor_name": result.get("actor_name"),
        },
    )


# @mcp.tool()
# def add_nodes_to_blueprint_bulk(blueprint_path: str, function_id: str, nodes: list) -> str:
#     """
#     Add multiple nodes to a Blueprint graph in a single operation
# 
#     Args:
#         blueprint_path: Path to the Blueprint asset
#         function_id: ID of the function to add the nodes to
#         nodes: Array of node definitions, each containing:
#             - id: ID for referencing the node (string) - this is important for creating connections later
#             - node_type: Type of node to add (see add_node_to_blueprint for supported types)
#             - node_position: Position of the node in the graph [X, Y]
#             - node_properties: Properties to set on the node (optional)
# 
#     Returns:
#         On success: Dictionary mapping your node IDs to the actual node GUIDs created in Unreal
#         On partial success: Dictionary with successful nodes and suggestions for failed nodes
#         On failure: Error message with suggestions
# 
#     Example success response:
#         {
#           "success": true,
#           "nodes": {
#             "function_entry": "425E7A3949D7420A461175A4733BBA5C",
#             "multiply_node": "70354A7E444BB68EEF31718DC50CF89C",
#             "return_node": "6436796645ED674F3C64A8A94CBA416C"
#           }
#         }
# 
#     Example partial success with suggestions:
#         {
#           "success": true,
#           "partial_success": true,
#           "nodes": {
#             "function_entry": "425E7A3949D7420A461175A4733BBA5C",
#             "return_node": "6436796645ED674F3C64A8A94CBA416C"
#           },
#           "suggestions": {
#             "multiply_node": {
#               "requested_type": "Multiply_Float",
#               "suggestions": ["KismetMathLibrary.Multiply_FloatFloat", "KismetMathLibrary.MultiplyByFloat"]
#             }
#           }
#         }
# 
#     When you receive suggestions, you can retry adding those nodes using the suggested node types.
#     """
#     command = {
#         "type": "add_nodes_bulk",
#         "blueprint_path": blueprint_path,
#         "function_id": function_id,
#         "nodes": nodes
#     }
# 
#     response = send_to_unreal(command)
#     if response.get("success"):
#         node_mapping = response.get("nodes", {})
#         return f"Successfully added {len(node_mapping)} nodes to function {function_id} in Blueprint at {blueprint_path}\nNode mapping: {json.dumps(node_mapping, indent=2)}"
#     else:
#         return f"Failed to add nodes: {response.get('error', 'Unknown error')}"

@mcp.tool()
def add_component_with_events(blueprint_path: str, component_name: str, component_class: str) -> dict:
    """
    Add a component to a Blueprint with overlap events if applicable.

    Args:
        blueprint_path: Path to the Blueprint (e.g., "/Game/FlappyBird/BP_FlappyBird")
        component_name: Name of the new component (e.g., "TriggerBox")
        component_class: Class of the component (e.g., "BoxComponent")

    Returns:
        Structured response envelope dict. Success payloads include `success=True`,
        a human-readable `message`, and `data` containing the tool name, component
        details, parsed overlap event GUIDs, and the raw Unreal response. Failure
        payloads include `success=False`, `error`, `error_code`, and diagnostic data.
    """
    command = {
        "type": "add_component_with_events",
        "blueprint_path": blueprint_path,
        "component_name": component_name,
        "component_class": component_class
    }
    response = send_to_unreal(command)
    result = _coerce_unreal_response(response)

    events = result.get("events")
    if isinstance(events, str):
        try:
            events = json.loads(events)
        except json.JSONDecodeError as exc:
            return _tool_error(
                f"Failed to parse overlap events from Unreal: {exc}",
                error_code="INVALID_EVENTS_RESPONSE",
                data={
                    "tool": "add_component_with_events",
                    "response": result,
                    "raw_events": result.get("events"),
                },
            )

    if result.get("success"):
        return _tool_success(
            result.get("message", f"Added component {component_name}."),
            data={
                "tool": "add_component_with_events",
                "blueprint_path": blueprint_path,
                "component_name": component_name,
                "component_class": component_class,
                "events": events if isinstance(events, dict) else {},
                "response": result,
            },
        )

    return _tool_error(
        result.get("error", "Failed to add component with events."),
        error_code=result.get("error_code", "ADD_COMPONENT_WITH_EVENTS_FAILED"),
        data={
            "tool": "add_component_with_events",
            "blueprint_path": blueprint_path,
            "component_name": component_name,
            "component_class": component_class,
            "response": result,
        },
    )


@mcp.tool()
def connect_blueprint_nodes_bulk(blueprint_path: str, function_id: str, connections: list) -> dict:
    """
    Connect multiple pairs of nodes in a Blueprint graph
    
    Args:
        blueprint_path: Path to the Blueprint asset
        function_id: ID of the function containing the nodes
        connections: Array of connection definitions, each containing:
            - source_node_id: ID of the source node
            - source_pin: Name of the source pin
            - target_node_id: ID of the target node
            - target_pin: Name of the target pin
        
    Returns:
        Message indicating success or failure, with details on which connections succeeded or failed
    """
    command = {
        "type": "connect_nodes_bulk",
        "blueprint_path": blueprint_path,
        "function_id": function_id,
        "connections": connections
    }

    return _command_tool_response(
        command,
        tool_name="connect_blueprint_nodes_bulk",
        success_message="Bulk Blueprint node connections completed.",
        error_code="CONNECT_NODES_BULK_FAILED",
        default_error="Failed to connect Blueprint nodes in bulk.",
        extra_data=lambda result: {
            "blueprint_path": blueprint_path,
            "function_id": function_id,
            "connections": connections,
            "successful_connections": result.get("successful_connections", 0),
            "total_connections": result.get("total_connections", 0),
            "results": result.get("results", []),
        },
    )


@mcp.tool()
def get_blueprint_node_guid(blueprint_path: str, graph_type: str = "EventGraph", node_name: str = None,
                            function_id: str = None) -> dict:
    """
    Retrieve the GUID of a pre-existing node in a Blueprint graph.
    
    Args:
        blueprint_path: Path to the Blueprint asset (e.g., "/Game/Blueprints/TestBulkBlueprint")
        graph_type: Type of graph to query ("EventGraph" or "FunctionGraph", default: "EventGraph")
        node_name: Name of the node to find (e.g., "BeginPlay" for EventGraph, optional if using function_id)
        function_id: ID of the function to get the FunctionEntry node for (optional, used with graph_type="FunctionGraph")
    
    Returns:
        Message with the node's GUID or an error if not found
    """
    command = {
        "type": "get_node_guid",
        "blueprint_path": blueprint_path,
        "graph_type": graph_type,
        "node_name": node_name if node_name else "",
        "function_id": function_id if function_id else ""
    }

    return _command_tool_response(
        command,
        tool_name="get_blueprint_node_guid",
        success_message="Blueprint node GUID loaded.",
        error_code="GET_BLUEPRINT_NODE_GUID_FAILED",
        default_error="Failed to get Blueprint node GUID.",
        extra_data=lambda result: {
            "blueprint_path": blueprint_path,
            "graph_type": graph_type,
            "node_name": node_name,
            "function_id": function_id,
            "node_guid": result.get("node_guid"),
        },
    )


# Safety check for potentially destructive actions
def is_potentially_destructive(script: str) -> bool:
    """
    Check if the script contains potentially destructive actions like deleting or saving files.
    Returns True if such actions are detected and not explicitly requested.
    """
    destructive_keywords = [
        r'unreal\.EditorAssetLibrary\.delete_asset',
        r'unreal\.EditorLevelLibrary\.destroy_actor',
        r'unreal\.save_package',
        r'os\.remove',
        r'shutil\.rmtree',
        r'file\.write',
        r'unreal\.EditorAssetLibrary\.save_asset'
    ]
    for keyword in destructive_keywords:
        if re.search(keyword, script, re.IGNORECASE):
            return True
    return False


@mcp.tool()
def enable_plugin(plugin_name: str, target_allow_list: list = None) -> dict:
    """
    Enable a plugin in the current Unreal project's .uproject file.

    Args:
        plugin_name: The plugin name as Unreal expects it in the descriptor.
        target_allow_list: Optional list such as ["Editor"].

    Returns:
        Message indicating success or failure.
    """
    return _command_tool_response(
        {
            "type": "set_plugin_enabled",
            "plugin_name": plugin_name,
            "enabled": True,
            "target_allow_list": target_allow_list,
        },
        tool_name="enable_plugin",
        success_message=f"Enabled plugin '{plugin_name}'.",
        error_code="ENABLE_PLUGIN_FAILED",
        default_error=f"Failed to enable plugin '{plugin_name}'.",
        extra_data=lambda result: {
            "plugin_name": plugin_name,
            "target_allow_list": target_allow_list,
            "project_file_path": result.get("project_file_path"),
            "restart_required": result.get("restart_required", False),
        },
    )


@mcp.tool()
def restart_editor(force: bool = False, wait_for_reconnect: bool = True,
                   timeout_seconds: float = 180.0, editor_path: str = "") -> dict:
    """
    Restart the current Unreal Editor session.

    Args:
        force: Restart even if Unreal reports unsaved assets or maps.
        wait_for_reconnect: Poll until the restarted editor reconnects to the MCP socket.
        timeout_seconds: Maximum time to wait for reconnect when wait_for_reconnect is True.
        editor_path: Optional explicit Unreal Editor executable path override.

    Returns:
        Structured response envelope dict. Success payloads include `success=True`,
        a human-readable `message`, and `data` containing the tool name and restart
        response details. Failure payloads include `success=False`, `error`,
        `error_code`, and restart metadata such as confirmation requirements,
        dirty packages, and suggested retry information when present.
    """
    result = _restart_editor_impl(force, wait_for_reconnect, timeout_seconds, editor_path)
    if result.get("success"):
        return _tool_success(
            result.get("message", "Editor restart scheduled."),
            data={
                "tool": "restart_editor",
                "response": result,
            },
        )

    error_code = result.get("error_code", "RESTART_EDITOR_FAILED")
    if result.get("confirmation_required"):
        error_code = "RESTART_CONFIRMATION_REQUIRED"

    return _tool_error(
        _format_restart_failure(result),
        error_code=error_code,
        data={
            "tool": "restart_editor",
            "response": result,
        },
        confirmation_required=result.get("confirmation_required", False),
        reason=result.get("reason"),
        dirty_packages=result.get("dirty_packages", []),
        dirty_package_count=result.get("dirty_package_count", 0),
        suggested_retry=result.get("suggested_retry"),
    )


@mcp.tool()
def enable_plugin_and_restart(plugin_name: str, force: bool = False, wait_for_reconnect: bool = True,
                              timeout_seconds: float = 180.0, editor_path: str = "",
                              target_allow_list: list = None) -> dict:
    """
    Enable a plugin in the current project and restart Unreal Editor if the descriptor changed.

    Args:
        plugin_name: The plugin name as Unreal expects it in the descriptor.
        force: Restart even if Unreal reports unsaved assets or maps.
        wait_for_reconnect: Poll until the restarted editor reconnects to the MCP socket.
        timeout_seconds: Maximum time to wait for reconnect when wait_for_reconnect is True.
        editor_path: Optional explicit Unreal Editor executable path override.
        target_allow_list: Optional list such as ["Editor"].

    Returns:
        Structured response envelope dict. Success payloads include `success=True`,
        a human-readable `message`, and `data` containing the tool name plus the
        enable and restart responses. Failure payloads include `success=False`,
        `error`, `error_code`, and restart confirmation metadata when present.
    """
    response = send_to_unreal({
        "type": "set_plugin_enabled",
        "plugin_name": plugin_name,
        "enabled": True,
        "target_allow_list": target_allow_list,
    })
    result = _coerce_unreal_response(response)

    if not result.get("success"):
        return _tool_error(
            result.get("error", f"Failed to enable plugin '{plugin_name}'."),
            error_code=result.get("error_code", "ENABLE_PLUGIN_FAILED"),
            data={
                "tool": "enable_plugin_and_restart",
                "plugin_name": plugin_name,
                "response": result,
            },
        )

    enable_message = result.get("message", f"Enabled plugin '{plugin_name}'.")
    if not result.get("restart_required"):
        return _tool_success(
            enable_message,
            data={
                "tool": "enable_plugin_and_restart",
                "plugin_name": plugin_name,
                "response": result,
            },
        )

    restart_result = _restart_editor_impl(force, wait_for_reconnect, timeout_seconds, editor_path)
    if restart_result.get("success"):
        return _tool_success(
            f"{enable_message} {restart_result.get('message', 'Editor restarted successfully.')}",
            data={
                "tool": "enable_plugin_and_restart",
                "plugin_name": plugin_name,
                "enable_response": result,
                "restart_response": restart_result,
            },
        )

    error_code = restart_result.get("error_code", "ENABLE_PLUGIN_AND_RESTART_FAILED")
    if restart_result.get("confirmation_required"):
        error_code = "RESTART_CONFIRMATION_REQUIRED"

    return _tool_error(
        f"{enable_message} {_format_restart_failure(restart_result)}",
        error_code=error_code,
        data={
            "tool": "enable_plugin_and_restart",
            "plugin_name": plugin_name,
            "enable_response": result,
            "restart_response": restart_result,
        },
        confirmation_required=restart_result.get("confirmation_required", False),
        reason=restart_result.get("reason"),
        dirty_packages=restart_result.get("dirty_packages", []),
        dirty_package_count=restart_result.get("dirty_package_count", 0),
        suggested_retry=restart_result.get("suggested_retry"),
    )


@mcp.tool()
def get_capabilities() -> dict:
    response = _coerce_unreal_response(send_to_unreal({"type": "get_capabilities"}))
    if response.get("success"):
        return ok("Capabilities loaded.", data=response, warnings=_response_warnings(response))
    return err(
        response.get("error", "Failed to load capabilities."),
        error_code=response.get("error_code", "CAPABILITIES_FAILED"),
        data=response,
        warnings=_response_warnings(response),
    )


@mcp.tool()
def preflight_project(
    required_plugins: list = None,
    required_editor_scripting_dependencies: list = None,
) -> dict:
    """
    Run project preflight checks before attempting higher-risk Unreal mutations.

    Args:
        required_plugins: Optional project plugins that must be enabled for the intended workflow.
        required_editor_scripting_dependencies: Optional editor scripting plugins to verify explicitly.

    Returns:
        Structured response envelope dict. Success payloads include `success=True`,
        a human-readable `message`, and `data` containing the preflight summary.
        Failure payloads include `success=False`, `error`, `error_code`, and
        any normalized Unreal diagnostics. If Unreal already returns an envelope,
        this tool forwards it without double-wrapping.
    """
    command = {"type": "preflight_project"}
    if required_plugins is not None:
        command["required_plugins"] = required_plugins
    if required_editor_scripting_dependencies is not None:
        command["required_editor_scripting_dependencies"] = required_editor_scripting_dependencies

    response = _coerce_unreal_response(send_to_unreal(command))
    if _looks_like_structured_envelope(response):
        return response
    return _forward_structured_socket_response(
        response,
        success_message="Preflight complete.",
        error_code="PREFLIGHT_FAILED",
        default_error="Failed to run project preflight.",
    )


@mcp.tool()
def get_job_status(job_id: str) -> dict:
    """
    Retrieve the current state of an Unreal MCP background job.

    Args:
        job_id: The job identifier returned by a pending tool response.

    Returns:
        Structured response envelope dict. Success payloads include `success=True`,
        a human-readable `message`, and `data` containing the socket server's
        normalized job status payload. Failure payloads include `success=False`,
        `error`, `error_code`, and any normalized diagnostics.
    """
    return _forward_structured_socket_response(
        send_to_unreal({"type": "get_job_status", "job_id": job_id}),
        success_message="Job status loaded.",
        error_code="JOB_STATUS_FAILED",
        default_error="Failed to load job status.",
    )


@mcp.tool()
def cancel_job(job_id: str) -> dict:
    """
    Attempt to cancel an Unreal MCP background job before it finishes.

    Args:
        job_id: The job identifier returned by a pending tool response.

    Returns:
        Structured response envelope dict. Success payloads include `success=True`,
        a human-readable `message`, and `data` containing the cancellation result.
        Failure payloads include `success=False`, `error`, `error_code`, and
        any normalized diagnostics.
    """
    response = _coerce_unreal_response(send_to_unreal({"type": "cancel_job", "job_id": job_id}))
    if response.get("status") == "cancelled":
        return ok(
            response.get("message", "Job cancelled."),
            data=response,
            warnings=_response_warnings(response),
        )
    return _forward_structured_socket_response(
        response,
        success_message="Job cancelled.",
        error_code="CANCEL_JOB_FAILED",
        default_error="Failed to cancel job.",
    )


@mcp.tool()
def list_active_jobs() -> dict:
    """
    List Unreal MCP jobs that are still queued or running.

    Returns:
        Structured response envelope dict. Success payloads include `success=True`,
        a human-readable `message`, and `data` containing the active job list.
        Failure payloads include `success=False`, `error`, `error_code`, and
        any normalized diagnostics.
    """
    return _forward_structured_socket_response(
        send_to_unreal({"type": "list_active_jobs"}),
        success_message="Active jobs loaded.",
        error_code="LIST_ACTIVE_JOBS_FAILED",
        default_error="Failed to list active jobs.",
    )


# Safe Mutation Runtime (P1)
@mcp.tool()
def preview_operation(operation: str, payload: dict = None) -> dict:
    """Run the preview phase of a safe mutation transaction.

    Args:
        operation: Name of the operation to preview (e.g. ``set_property``,
            ``add_component``, ``compile_blueprint``, or ``generic`` for
            arbitrary asset edits backed by ``UGenAssetTransactionUtils``).
        payload: Operation-specific arguments. Pass ``target_assets`` (list of
            asset paths) and any ``changes`` you intend to apply.

    Returns:
        Structured envelope. The ``data`` block always contains
        ``transaction_id``, which must be passed to ``apply_operation``.
    """
    command = {"type": "preview_operation", "operation": operation}
    if payload is not None:
        command["payload"] = payload
    return _forward_structured_socket_response(
        send_to_unreal(command),
        success_message="Preview ready.",
        error_code="PREVIEW_FAILED",
        default_error="Failed to preview operation.",
    )


@mcp.tool()
def apply_operation(transaction_id: str, payload: dict = None) -> dict:
    """Apply a previously previewed mutation transaction.

    Args:
        transaction_id: The token returned by ``preview_operation``.
        payload: Optional final overrides (kept aligned with the previewed
            ``changes``). ``snapshot_token`` and ``target_assets`` are inherited
            automatically when omitted.

    Returns:
        Structured envelope whose ``data`` block contains a mutation report
        (``changed_assets``, ``compiled_assets``, ``saved_assets``,
        ``warnings``, ``rollback_performed``, ``verification_checks``).
    """
    command = {"type": "apply_operation", "transaction_id": transaction_id}
    if payload is not None:
        command["payload"] = payload
    return _forward_structured_socket_response(
        send_to_unreal(command),
        success_message="Operation applied.",
        error_code="APPLY_FAILED",
        default_error="Failed to apply operation.",
    )


@mcp.tool()
def undo_last_mcp_operation() -> dict:
    """Roll back the most recent MCP-originated mutation transaction."""

    return _forward_structured_socket_response(
        send_to_unreal({"type": "undo_last_mcp_operation"}),
        success_message="Last MCP operation undone.",
        error_code="UNDO_FAILED",
        default_error="Failed to undo last MCP operation.",
    )


# Blueprint Inspection (P1.25)
@mcp.tool()
def get_graph_schema(blueprint_path: str) -> dict:
    """Return all graphs (Ubergraph, Function, Macro, Animation, ...) for a Blueprint."""

    return _forward_structured_socket_response(
        send_to_unreal({"type": "get_graph_schema", "blueprint_path": blueprint_path}),
        success_message="Graph schema loaded.",
        error_code="GRAPH_SCHEMA_FAILED",
        default_error="Failed to load graph schema.",
    )


@mcp.tool()
def resolve_graph_by_path(blueprint_path: str, graph_path: str) -> dict:
    """Resolve a graph by its hierarchical path (e.g. ``EventGraph/MyFunction``)."""

    return _forward_structured_socket_response(
        send_to_unreal(
            {
                "type": "resolve_graph_by_path",
                "blueprint_path": blueprint_path,
                "graph_path": graph_path,
            }
        ),
        success_message="Graph resolved.",
        error_code="RESOLVE_GRAPH_FAILED",
        default_error="Failed to resolve graph.",
    )


@mcp.tool()
def get_graph_nodes(blueprint_path: str, graph_path: str) -> dict:
    """List nodes within a specific graph."""

    return _forward_structured_socket_response(
        send_to_unreal(
            {
                "type": "get_graph_nodes",
                "blueprint_path": blueprint_path,
                "graph_path": graph_path,
            }
        ),
        success_message="Graph nodes loaded.",
        error_code="GRAPH_NODES_FAILED",
        default_error="Failed to load graph nodes.",
    )


@mcp.tool()
def get_graph_pins(blueprint_path: str, graph_path: str, node_guid: str) -> dict:
    """List pins of a node, including direction, category, and sub-category."""

    return _forward_structured_socket_response(
        send_to_unreal(
            {
                "type": "get_graph_pins",
                "blueprint_path": blueprint_path,
                "graph_path": graph_path,
                "node_guid": node_guid,
            }
        ),
        success_message="Graph pins loaded.",
        error_code="GRAPH_PINS_FAILED",
        default_error="Failed to load graph pins.",
    )


@mcp.tool()
def resolve_node_by_selector(blueprint_path: str, selector: str) -> dict:
    """Resolve a node by selector (``GUID``, ``Name``, or ``Graph:Identifier``)."""

    return _forward_structured_socket_response(
        send_to_unreal(
            {
                "type": "resolve_node_by_selector",
                "blueprint_path": blueprint_path,
                "selector": selector,
            }
        ),
        success_message="Node resolved.",
        error_code="NODE_RESOLVE_FAILED",
        default_error="Failed to resolve node.",
    )


@mcp.tool()
def get_pin_compatibility(
    source_pin: dict,
    target_pin: dict,
    source_node: str = "",
    target_node: str = "",
) -> dict:
    """Return whether two pins can be connected and, if not, an autocast hint.

    Each pin descriptor must include ``name``, ``direction`` (``input`` /
    ``output``), and ``category``. Optional fields: ``sub_category``,
    ``container_type`` (``none`` / ``array`` / ``set`` / ``map``),
    ``is_reference``.
    """

    return _forward_structured_socket_response(
        send_to_unreal(
            {
                "type": "get_pin_compatibility",
                "source_pin": source_pin,
                "target_pin": target_pin,
                "source_node": source_node,
                "target_node": target_node,
            }
        ),
        success_message="Pin compatibility evaluated.",
        error_code="PIN_INCOMPATIBLE",
        default_error="Failed to evaluate pin compatibility.",
    )


@mcp.tool()
def suggest_autocast_path(source_category: str, target_category: str) -> dict:
    """Return the conversion node hint for casting between two pin categories."""

    return _forward_structured_socket_response(
        send_to_unreal(
            {
                "type": "suggest_autocast_path",
                "source_category": source_category,
                "target_category": target_category,
            }
        ),
        success_message="Autocast suggestion ready.",
        error_code="AUTOCAST_UNAVAILABLE",
        default_error="No autocast suggestion available.",
    )


@mcp.tool()
def compile_blueprint_with_diagnostics(blueprint_path: str) -> dict:
    """Compile a Blueprint and return structured warnings/errors instead of bool."""

    return _forward_structured_socket_response(
        send_to_unreal(
            {"type": "compile_blueprint_with_diagnostics", "blueprint_path": blueprint_path}
        ),
        success_message="Blueprint compiled.",
        error_code="BLUEPRINT_COMPILE_FAILED",
        default_error="Failed to compile Blueprint.",
    )


# -----------------------------------------------------------------------------
# Editor session restore (P1.5)
# -----------------------------------------------------------------------------

@mcp.tool()
def capture_editor_session() -> dict:
    """Snapshot the current editor state (open assets, primary focus, graph)."""
    return _forward_structured_socket_response(
        send_to_unreal({"type": "capture_editor_session"}),
        success_message="Session captured.",
        error_code="SESSION_CAPTURE_FAILED",
        default_error="Unable to capture editor session.",
    )


@mcp.tool()
def save_editor_session(snapshot: dict | None = None) -> dict:
    """Persist the provided (or freshly captured) session to Saved/MCP/LastEditorSession.json."""
    command = {"type": "save_editor_session"}
    if snapshot is not None:
        command["snapshot"] = snapshot
    return _forward_structured_socket_response(
        send_to_unreal(command),
        success_message="Session saved.",
        error_code="SESSION_SAVE_FAILED",
        default_error="Unable to save editor session.",
    )


@mcp.tool()
def restore_editor_session(policy: str = "assets_only", snapshot: dict | None = None) -> dict:
    """Re-open the assets / graph / node focus captured in the last session snapshot.

    ``policy`` is one of ``none``, ``assets_only`` (default), ``assets_and_tabs``.
    """
    command = {"type": "restore_editor_session", "policy": policy}
    if snapshot is not None:
        command["snapshot"] = snapshot
    return _forward_structured_socket_response(
        send_to_unreal(command),
        success_message="Session restored.",
        error_code="SESSION_RESTORE_FAILED",
        default_error="Unable to restore editor session.",
    )


@mcp.tool()
def open_asset(asset_path: str, primary: bool = False) -> dict:
    """Open an asset in its default editor."""
    return _forward_structured_socket_response(
        send_to_unreal({"type": "open_asset", "asset_path": asset_path, "primary": primary}),
        success_message="Asset opened.",
        error_code="OPEN_ASSET_FAILED",
        default_error="Unable to open asset.",
    )


@mcp.tool()
def bring_asset_to_front(asset_path: str) -> dict:
    """Bring the asset editor tab to the foreground."""
    return _forward_structured_socket_response(
        send_to_unreal({"type": "bring_asset_to_front", "asset_path": asset_path}),
        success_message="Asset brought to front.",
        error_code="FOCUS_ASSET_FAILED",
        default_error="Unable to focus asset.",
    )


@mcp.tool()
def focus_graph(asset_path: str, graph_path: str) -> dict:
    """Switch the focused graph tab inside the Blueprint editor."""
    return _forward_structured_socket_response(
        send_to_unreal(
            {"type": "focus_graph", "asset_path": asset_path, "graph_path": graph_path}
        ),
        success_message="Graph focused.",
        error_code="FOCUS_GRAPH_FAILED",
        default_error="Unable to focus graph.",
    )


@mcp.tool()
def focus_node(asset_path: str, graph_path: str, node_guid: str) -> dict:
    """Center the graph viewport on a specific node by GUID."""
    return _forward_structured_socket_response(
        send_to_unreal(
            {
                "type": "focus_node",
                "asset_path": asset_path,
                "graph_path": graph_path,
                "node_guid": node_guid,
            }
        ),
        success_message="Node focused.",
        error_code="FOCUS_NODE_FAILED",
        default_error="Unable to focus node.",
    )


@mcp.tool()
def select_actor(actor_label: str) -> dict:
    """Select a level actor by label or path."""
    return _forward_structured_socket_response(
        send_to_unreal({"type": "select_actor", "actor_label": actor_label}),
        success_message="Actor selected.",
        error_code="SELECT_ACTOR_FAILED",
        default_error="Unable to select actor.",
    )


# -----------------------------------------------------------------------------
# Enhanced Input (P2)
# -----------------------------------------------------------------------------

@mcp.tool()
def create_input_action(
    name: str,
    save_path: str = "/Game/Input",
    value_type: str = "Digital",
    description: str = "",
) -> dict:
    """Create a UInputAction asset.  ``value_type`` in {Digital, Axis1D, Axis2D, Axis3D}."""
    return _forward_structured_socket_response(
        send_to_unreal(
            {
                "type": "create_input_action",
                "name": name,
                "save_path": save_path,
                "value_type": value_type,
                "description": description,
            }
        ),
        success_message="InputAction created.",
        error_code="CREATE_INPUT_ACTION_FAILED",
        default_error="Unable to create InputAction.",
    )


@mcp.tool()
def create_input_mapping_context(name: str, save_path: str = "/Game/Input") -> dict:
    """Create a UInputMappingContext asset."""
    return _forward_structured_socket_response(
        send_to_unreal(
            {
                "type": "create_input_mapping_context",
                "name": name,
                "save_path": save_path,
            }
        ),
        success_message="InputMappingContext created.",
        error_code="CREATE_INPUT_CONTEXT_FAILED",
        default_error="Unable to create InputMappingContext.",
    )


@mcp.tool()
def map_enhanced_input_action(
    context_path: str,
    action_path: str,
    key: str,
    triggers: list | None = None,
    modifiers: list | None = None,
) -> dict:
    """Map ``key`` (with optional triggers / modifiers) to an InputAction in a context."""
    return _forward_structured_socket_response(
        send_to_unreal(
            {
                "type": "map_enhanced_input_action",
                "context_path": context_path,
                "action_path": action_path,
                "key": key,
                "triggers": triggers or [],
                "modifiers": modifiers or [],
            }
        ),
        success_message="Input mapping added.",
        error_code="INPUT_MAPPING_FAILED",
        default_error="Unable to add input mapping.",
    )


@mcp.tool()
def list_input_mappings(context_path: str) -> dict:
    """Return the bindings inside an InputMappingContext."""
    return _forward_structured_socket_response(
        send_to_unreal({"type": "list_input_mappings", "context_path": context_path}),
        success_message="Mappings listed.",
        error_code="LIST_INPUT_MAPPINGS_FAILED",
        default_error="Unable to list input mappings.",
    )


# -----------------------------------------------------------------------------
# BlendSpace (P3)
# -----------------------------------------------------------------------------

@mcp.tool()
def get_blend_space_info(blend_space_path: str) -> dict:
    """Read axes / samples / additive settings of a BlendSpace asset."""
    return _forward_structured_socket_response(
        send_to_unreal(
            {"type": "get_blend_space_info", "blend_space_path": blend_space_path}
        ),
        success_message="BlendSpace info read.",
        error_code="BLEND_SPACE_READ_FAILED",
        default_error="Unable to read BlendSpace.",
    )


@mcp.tool()
def set_blend_space_axis(blend_space_path: str, axis_index: int, axis: dict) -> dict:
    """Replace a single axis config.  ``axis`` keys: name, min_value, max_value, grid_divisions, kind."""
    return _forward_structured_socket_response(
        send_to_unreal(
            {
                "type": "set_blend_space_axis",
                "blend_space_path": blend_space_path,
                "axis_index": axis_index,
                "axis": axis,
            }
        ),
        success_message="BlendSpace axis updated.",
        error_code="BLEND_SPACE_WRITE_FAILED",
        default_error="Unable to update BlendSpace axis.",
    )


@mcp.tool()
def replace_blend_space_samples(blend_space_path: str, samples: list) -> dict:
    """Replace the full sample set (each sample: animation_path, coordinates, rate_scale)."""
    return _forward_structured_socket_response(
        send_to_unreal(
            {
                "type": "replace_blend_space_samples",
                "blend_space_path": blend_space_path,
                "samples": samples,
            }
        ),
        success_message="BlendSpace samples replaced.",
        error_code="BLEND_SPACE_WRITE_FAILED",
        default_error="Unable to replace BlendSpace samples.",
    )


@mcp.tool()
def set_blend_space_sample_animation(
    blend_space_path: str,
    sample_index: int,
    animation_path: str,
) -> dict:
    """Swap the animation asset referenced by an existing BlendSpace sample."""
    return _forward_structured_socket_response(
        send_to_unreal(
            {
                "type": "set_blend_space_sample_animation",
                "blend_space_path": blend_space_path,
                "sample_index": sample_index,
                "animation_path": animation_path,
            }
        ),
        success_message="BlendSpace sample animation updated.",
        error_code="BLEND_SPACE_WRITE_FAILED",
        default_error="Unable to update BlendSpace sample animation.",
    )


# ---------------------------------------------------------------------------
# AnimBlueprint read (P4) ---------------------------------------------------
# ---------------------------------------------------------------------------


@mcp.tool()
def get_anim_blueprint_structure(anim_blueprint_path: str) -> dict:
    """Return a deterministic snapshot of an AnimBlueprint (state machines, states, transitions, aliases)."""
    return _forward_structured_socket_response(
        send_to_unreal({"type": "get_anim_blueprint_structure", "anim_blueprint_path": anim_blueprint_path}),
        success_message="AnimBlueprint structure retrieved.",
        error_code="ANIM_BP_READ_FAILED",
        default_error="Unable to read AnimBlueprint structure.",
    )


@mcp.tool()
def get_anim_graph_nodes(anim_blueprint_path: str, graph_path: str) -> dict:
    """List nodes inside an AnimBlueprint graph path (e.g. ``AnimGraph/Locomotion``)."""
    return _forward_structured_socket_response(
        send_to_unreal({"type": "get_anim_graph_nodes", "anim_blueprint_path": anim_blueprint_path, "graph_path": graph_path}),
        success_message="Graph nodes retrieved.",
        error_code="ANIM_BP_READ_FAILED",
        default_error="Unable to read graph nodes.",
    )


@mcp.tool()
def get_anim_graph_pins(anim_blueprint_path: str, graph_path: str, node_id: str) -> dict:
    """List pins of a specific node inside an AnimBlueprint graph."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "get_anim_graph_pins",
            "anim_blueprint_path": anim_blueprint_path,
            "graph_path": graph_path,
            "node_id": node_id,
        }),
        success_message="Graph pins retrieved.",
        error_code="ANIM_BP_READ_FAILED",
        default_error="Unable to read graph pins.",
    )


@mcp.tool()
def resolve_anim_graph_by_path(anim_blueprint_path: str, graph_path: str) -> dict:
    """Resolve an AnimBlueprint graph path to stable selectors."""
    return _forward_structured_socket_response(
        send_to_unreal({"type": "resolve_anim_graph_by_path", "anim_blueprint_path": anim_blueprint_path, "graph_path": graph_path}),
        success_message="Graph resolved.",
        error_code="ANIM_BP_READ_FAILED",
        default_error="Unable to resolve graph path.",
    )


# ---------------------------------------------------------------------------
# AnimBlueprint write (P5) --------------------------------------------------
# ---------------------------------------------------------------------------


@mcp.tool()
def create_state_machine(anim_blueprint_path: str, state_machine: str, entry_state: str = "") -> dict:
    """Create a new state machine inside an AnimBlueprint's AnimGraph."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "create_state_machine",
            "anim_blueprint_path": anim_blueprint_path,
            "state_machine": state_machine,
            "entry_state": entry_state,
        }),
        success_message="State machine created.",
        error_code="ANIM_BP_WRITE_FAILED",
        default_error="Unable to create state machine.",
    )


@mcp.tool()
def create_state(anim_blueprint_path: str, state_machine: str, state: str, kind: str = "State") -> dict:
    """Create a new state inside a state machine."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "create_state",
            "anim_blueprint_path": anim_blueprint_path,
            "state_machine": state_machine,
            "state": state,
            "kind": kind,
        }),
        success_message="State created.",
        error_code="ANIM_BP_WRITE_FAILED",
        default_error="Unable to create state.",
    )


@mcp.tool()
def create_transition(anim_blueprint_path: str, state_machine: str, from_state: str, to_state: str, rule: dict = None) -> dict:
    """Create a transition between two states. ``rule`` follows the transition rule schema."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "create_transition",
            "anim_blueprint_path": anim_blueprint_path,
            "state_machine": state_machine,
            "from_state": from_state,
            "to_state": to_state,
            "rule": rule or {"kind": "always"},
        }),
        success_message="Transition created.",
        error_code="ANIM_BP_WRITE_FAILED",
        default_error="Unable to create transition.",
    )


@mcp.tool()
def set_transition_rule(anim_blueprint_path: str, state_machine: str, from_state: str, to_state: str, rule: dict) -> dict:
    """Set the rule on an existing transition."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "set_transition_rule",
            "anim_blueprint_path": anim_blueprint_path,
            "state_machine": state_machine,
            "from_state": from_state,
            "to_state": to_state,
            "rule": rule,
        }),
        success_message="Transition rule updated.",
        error_code="ANIM_BP_WRITE_FAILED",
        default_error="Unable to update transition rule.",
    )


@mcp.tool()
def create_state_alias(anim_blueprint_path: str, state_machine: str, alias_name: str, aliased_states: list) -> dict:
    """Create a state alias that forwards to the listed states."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "create_state_alias",
            "anim_blueprint_path": anim_blueprint_path,
            "state_machine": state_machine,
            "state": alias_name,
            "aliased_states": list(aliased_states or []),
        }),
        success_message="State alias created.",
        error_code="ANIM_BP_WRITE_FAILED",
        default_error="Unable to create state alias.",
    )


@mcp.tool()
def set_alias_targets(anim_blueprint_path: str, state_machine: str, alias_name: str, aliased_states: list) -> dict:
    """Replace the target state list of an existing alias."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "set_alias_targets",
            "anim_blueprint_path": anim_blueprint_path,
            "state_machine": state_machine,
            "state": alias_name,
            "aliased_states": list(aliased_states or []),
        }),
        success_message="Alias targets updated.",
        error_code="ANIM_BP_WRITE_FAILED",
        default_error="Unable to update alias targets.",
    )


@mcp.tool()
def set_state_sequence_asset(
    anim_blueprint_path: str,
    state_machine: str,
    state: str,
    asset_path: str,
    play_rate: float = 1.0,
    play_mode: str = "Loop",
) -> dict:
    """Bind a state's inner pose to an AnimSequence asset."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "set_state_sequence_asset",
            "anim_blueprint_path": anim_blueprint_path,
            "state_machine": state_machine,
            "state": state,
            "asset_path": asset_path,
            "play_rate": play_rate,
            "play_mode": play_mode,
        }),
        success_message="State sequence asset set.",
        error_code="ANIM_BP_WRITE_FAILED",
        default_error="Unable to set state sequence asset.",
    )


@mcp.tool()
def set_state_blend_space_asset(
    anim_blueprint_path: str,
    state_machine: str,
    state: str,
    asset_path: str,
    play_rate: float = 1.0,
    play_mode: str = "Loop",
) -> dict:
    """Bind a state's inner pose to a BlendSpace asset."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "set_state_blend_space_asset",
            "anim_blueprint_path": anim_blueprint_path,
            "state_machine": state_machine,
            "state": state,
            "asset_path": asset_path,
            "play_rate": play_rate,
            "play_mode": play_mode,
        }),
        success_message="State BlendSpace asset set.",
        error_code="ANIM_BP_WRITE_FAILED",
        default_error="Unable to set state BlendSpace asset.",
    )


@mcp.tool()
def set_cached_pose_node(anim_blueprint_path: str, pose_name: str, source_node: str = "") -> dict:
    """Create or update a Cached Pose node feeding from ``source_node``."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "set_cached_pose_node",
            "anim_blueprint_path": anim_blueprint_path,
            "pose_name": pose_name,
            "source_node": source_node,
        }),
        success_message="Cached pose node updated.",
        error_code="ANIM_BP_WRITE_FAILED",
        default_error="Unable to update cached pose node.",
    )


@mcp.tool()
def set_default_slot_chain(anim_blueprint_path: str, slot_name: str = "DefaultSlot", source_node: str = "") -> dict:
    """Wire a Default Slot node into the AnimGraph output."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "set_default_slot_chain",
            "anim_blueprint_path": anim_blueprint_path,
            "slot_name": slot_name,
            "source_node": source_node,
        }),
        success_message="Default slot chain updated.",
        error_code="ANIM_BP_WRITE_FAILED",
        default_error="Unable to update default slot chain.",
    )


@mcp.tool()
def set_apply_additive_chain(anim_blueprint_path: str, base_node: str, additive_node: str, alpha: float = 1.0) -> dict:
    """Insert an Apply Additive node combining ``base_node`` and ``additive_node``."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "set_apply_additive_chain",
            "anim_blueprint_path": anim_blueprint_path,
            "base_node": base_node,
            "additive_node": additive_node,
            "alpha": alpha,
        }),
        success_message="Apply additive chain updated.",
        error_code="ANIM_BP_WRITE_FAILED",
        default_error="Unable to update apply additive chain.",
    )


# Scene Control
@mcp.tool()
def get_all_scene_objects() -> dict:
    """
    Retrieve all actors in the current Unreal Engine level.
    
    Returns:
        JSON string of actors with their names, classes, and locations.
    """
    command = {"type": "get_all_scene_objects"}
    return _command_tool_response(
        command,
        tool_name="get_all_scene_objects",
        success_message="Scene objects loaded.",
        error_code="GET_ALL_SCENE_OBJECTS_FAILED",
        default_error="Failed to load scene objects.",
        extra_data=lambda result: {
            "actors": result.get("actors", []),
        },
    )


# Project Control
@mcp.tool()
def create_project_folder(folder_path: str) -> dict:
    """
    Create a new folder in the Unreal project content directory.
    
    Args:
        folder_path: Path relative to /Game (e.g., "FlappyBird/Assets")
    """
    command = {"type": "create_project_folder", "folder_path": folder_path}
    return _command_tool_response(
        command,
        tool_name="create_project_folder",
        success_message="Project folder created.",
        error_code="CREATE_PROJECT_FOLDER_FAILED",
        default_error="Failed to create project folder.",
        extra_data=lambda result: {
            "folder_path": folder_path,
        },
    )


@mcp.tool()
def get_files_in_folder(folder_path: str) -> dict:
    """
    List all files in a specified project folder.
    
    Args:
        folder_path: Path relative to /Game (e.g., "FlappyBird/Assets")
    """
    command = {"type": "get_files_in_folder", "folder_path": folder_path}
    return _command_tool_response(
        command,
        tool_name="get_files_in_folder",
        success_message="Folder contents loaded.",
        error_code="GET_FILES_IN_FOLDER_FAILED",
        default_error="Failed to list files in folder.",
        extra_data=lambda result: {
            "folder_path": folder_path,
            "files": result.get("files", []),
        },
    )


@mcp.tool()
def create_game_mode(game_mode_path: str, pawn_blueprint_path: str, base_class: str = "GameModeBase") -> dict:
    """Create a game mode Blueprint, set its default pawn, and assign it as the current scene’s default game mode.
    Args:
        game_mode_path: Path for new game mode (e.g., "/Game/MyGameMode")
        pawn_blueprint_path: Path to pawn Blueprint (e.g., "/Game/Blueprints/BP_Player")
        base_class: Base class for game mode (default: "GameModeBase")
    """
    try:
        return _command_tool_response(
            {
                "type": "create_game_mode",
                "game_mode_path": game_mode_path,
                "pawn_blueprint_path": pawn_blueprint_path,
                "base_class": base_class,
            },
            tool_name="create_game_mode",
            success_message="Game mode created successfully.",
            error_code="CREATE_GAME_MODE_FAILED",
            default_error="Failed to create game mode.",
            extra_data=lambda result: {
                "game_mode_path": game_mode_path,
                "pawn_blueprint_path": pawn_blueprint_path,
                "base_class": base_class,
            },
        )
    except Exception as e:
        return _tool_error(
            f"Error creating game mode: {str(e)}",
            error_code="CREATE_GAME_MODE_FAILED",
            data={
                "tool": "create_game_mode",
                "game_mode_path": game_mode_path,
                "pawn_blueprint_path": pawn_blueprint_path,
                "base_class": base_class,
            },
        )


@mcp.tool()
def add_widget_to_user_widget(user_widget_path: str, widget_type: str, widget_name: str,
                              parent_widget_name: str = "") -> dict:
    """
    Adds a new widget (like TextBlock, Button, Image, CanvasPanel, VerticalBox) to a User Widget Blueprint.

    Args:
        user_widget_path: Path to the User Widget Blueprint (e.g., "/Game/UI/WBP_MainMenu").
        widget_type: Class name of the widget to add (e.g., "TextBlock", "Button", "Image", "CanvasPanel", "VerticalBox", "HorizontalBox", "SizeBox", "Border"). Case-sensitive.
        widget_name: A unique desired name for the new widget variable (e.g., "TitleText", "StartButton", "PlayerHealthBar"). The actual name might get adjusted for uniqueness.
        parent_widget_name: Optional. The name of an existing Panel widget (like CanvasPanel, VerticalBox) inside the User Widget to attach this new widget to. If empty, attempts to attach to the root or the first available CanvasPanel.

    Returns:
        JSON string indicating success (with actual widget name) or failure with an error message.
    """
    return _command_tool_response(
        {
            "type": "add_widget_to_user_widget",
            "user_widget_path": user_widget_path,
            "widget_type": widget_type,
            "widget_name": widget_name,
            "parent_widget_name": parent_widget_name,
        },
        tool_name="add_widget_to_user_widget",
        success_message="Widget added to User Widget successfully.",
        error_code="ADD_WIDGET_FAILED",
        default_error="Failed to add widget to User Widget.",
        extra_data=lambda result: {
            "user_widget_path": user_widget_path,
            "widget_type": widget_type,
            "widget_name": widget_name,
            "parent_widget_name": parent_widget_name,
            "actual_widget_name": result.get("widget_name"),
        },
    )


@mcp.tool()
def edit_widget_property(user_widget_path: str, widget_name: str, property_name: str, value: str) -> dict:
    """
    Edits a property of a specific widget within a User Widget Blueprint.

    Args:
        user_widget_path: Path to the User Widget Blueprint (e.g., "/Game/UI/WBP_MainMenu").
        widget_name: The name of the widget inside the User Widget whose property you want to change (e.g., "TitleText", "StartButton").
        property_name: The name of the property to edit. For layout properties controlled by the parent panel (like position, size, anchors in a CanvasPanel), prefix with "Slot." (e.g., "Text", "ColorAndOpacity", "Brush.ImageSize", "Slot.Position", "Slot.Size", "Slot.Anchors", "Slot.Alignment"). Case-sensitive.
        value: The new value for the property, formatted as a string EXACTLY as Unreal expects for ImportText. Examples:
            - Text: '"Hello World!"' (Note: String literal requires inner quotes)
            - Float: '150.0'
            - Integer: '10'
            - Boolean: 'true' or 'false'
            - LinearColor: '(R=1.0,G=0.0,B=0.0,A=1.0)'
            - Vector2D (for Size, Position): '(X=200.0,Y=50.0)'
            - Anchors: '(Minimum=(X=0.5,Y=0.0),Maximum=(X=0.5,Y=0.0))' (Top Center Anchor)
            - Alignment (Vector2D): '(X=0.5,Y=0.5)' (Center Alignment)
            - Font (FSlateFontInfo): "(FontObject=Font'/Engine/EngineFonts/Roboto.Roboto',Size=24)"
            - Texture (Object Path): "Texture2D'/Game/Textures/MyIcon.MyIcon'"
            - Enum (e.g., Stretch): 'ScaleToFit'

    Returns:
        JSON string indicating success or failure with an error message.
    """
    return _command_tool_response(
        {
            "type": "edit_widget_property",
            "user_widget_path": user_widget_path,
            "widget_name": widget_name,
            "property_name": property_name,
            "value": value,
        },
        tool_name="edit_widget_property",
        success_message="Widget property updated successfully.",
        error_code="EDIT_WIDGET_PROPERTY_FAILED",
        default_error="Failed to edit widget property.",
        extra_data=lambda result: {
            "user_widget_path": user_widget_path,
            "widget_name": widget_name,
            "property_name": property_name,
            "value": value,
        },
    )


# Input
@mcp.tool()
def add_input_binding(action_name: str, key: str) -> dict:
    """
    Add an input action binding to Project Settings.
    
    Args:
        action_name: Name of the action (e.g., "Flap")
        key: Key to bind (e.g., "Space Bar")
    """
    return _command_tool_response(
        {"type": "add_input_binding", "action_name": action_name, "key": key},
        tool_name="add_input_binding",
        success_message="Input binding added successfully.",
        error_code="ADD_INPUT_BINDING_FAILED",
        default_error="Failed to add input binding.",
        extra_data=lambda result: {
            "action_name": action_name,
            "key": key,
        },
    )


def _resolve_fab_search(query: str, max_results: int, timeout_seconds: float) -> dict:
    start_response = send_to_unreal({
        "type": "start_fab_search",
        "query": query,
        "max_results": max_results,
        "timeout_seconds": timeout_seconds
    })

    if not isinstance(start_response, dict):
        return {"success": False, "status": "error", "error": "Invalid Fab search start response"}

    if start_response.get("status") in ("completed", "error"):
        return start_response

    operation_id = start_response.get("operation_id")
    if not operation_id:
        return {"success": False, "status": "error", "error": "Fab search did not return an operation_id"}

    return poll_fab_operation(operation_id, timeout_seconds)


def _resolve_fab_import(listing_id_or_url: str, timeout_seconds: float) -> dict:
    start_response = send_to_unreal({
        "type": "start_fab_add_to_project",
        "listing_id_or_url": listing_id_or_url,
        "timeout_seconds": timeout_seconds
    })

    if not isinstance(start_response, dict):
        return {"success": False, "status": "error", "error": "Invalid Fab import start response"}

    if start_response.get("status") in ("completed", "error"):
        return start_response

    operation_id = start_response.get("operation_id")
    if not operation_id:
        return {"success": False, "status": "error", "error": "Fab import did not return an operation_id"}

    return poll_fab_operation(operation_id, timeout_seconds)


def _extract_fab_results(search_response: dict) -> list:
    results = search_response.get("results", [])
    return results if isinstance(results, list) else []


def _select_fab_result(results: list, preferred_listing_id_or_url: str = "", preferred_title_substring: str = ""):
    listing_hint = (preferred_listing_id_or_url or "").strip().casefold()
    if listing_hint:
        for result in results:
            listing_id = str(result.get("listing_id", "")).casefold()
            listing_url = str(result.get("listing_url", "")).casefold()
            if listing_hint == listing_id or listing_hint == listing_url:
                return result, "preferred_listing"

    title_hint = (preferred_title_substring or "").strip().casefold()
    if title_hint:
        for result in results:
            title = str(result.get("title", "")).casefold()
            if title_hint in title:
                return result, "preferred_title"

    return (results[0], "first_verified_result") if results else (None, "")


@mcp.tool()
def search_free_fab_assets(query: str, max_results: int = 10, timeout_seconds: float = 60.0) -> dict:
    """
    Search Fab for free Unreal Engine content assets and return verified Add-to-Project results.

    Args:
        query: Search text to use on Fab.
        max_results: Maximum number of verified free results to return.
        timeout_seconds: Total time budget for the search plus listing verification flow.

    Returns:
        JSON string containing the final Fab search operation result.
    """
    return _tool_response_from_unreal(
        _resolve_fab_search(query, max_results, timeout_seconds),
        tool_name="search_free_fab_assets",
        success_message="Fab search completed.",
        error_code="FAB_SEARCH_FAILED",
        default_error="Fab search failed.",
        extra_data=lambda result: {
            "query": query,
            "max_results": max_results,
            "timeout_seconds": timeout_seconds,
            "results": result.get("results", []),
            "status": result.get("status"),
            "operation_id": result.get("operation_id"),
        },
    )


@mcp.tool()
def add_free_fab_asset_to_project(listing_id_or_url: str, timeout_seconds: float = 180.0) -> dict:
    """
    Add a free Fab asset to the current Unreal project using its listing id or listing URL.

    Args:
        listing_id_or_url: Fab listing id or listing URL from `search_free_fab_assets`.
        timeout_seconds: Total time budget for the add-to-project workflow.

    Returns:
        JSON string containing the final Fab import operation result.
    """
    return _tool_response_from_unreal(
        _resolve_fab_import(listing_id_or_url, timeout_seconds),
        tool_name="add_free_fab_asset_to_project",
        success_message="Fab asset import completed.",
        error_code="FAB_IMPORT_FAILED",
        default_error="Fab asset import failed.",
        extra_data=lambda result: {
            "listing_id_or_url": listing_id_or_url,
            "timeout_seconds": timeout_seconds,
            "import_path": result.get("import_path"),
            "status": result.get("status"),
            "operation_id": result.get("operation_id"),
        },
    )


@mcp.tool()
def search_and_add_free_fab_asset(
    query: str,
    preferred_listing_id_or_url: str = "",
    preferred_title_substring: str = "",
    max_results: int = 8,
    search_timeout_seconds: float = 60.0,
    import_timeout_seconds: float = 180.0
) -> dict:
    """
    Search Fab for a free content asset and immediately add a verified result to the current project.

    Args:
        query: Search text to use on Fab.
        preferred_listing_id_or_url: Optional exact listing id or URL to pick from the verified search results.
        preferred_title_substring: Optional case-insensitive title substring to prefer when selecting a result.
        max_results: Maximum number of verified candidates to inspect from the search.
        search_timeout_seconds: Total time budget for the search plus listing verification flow.
        import_timeout_seconds: Total time budget for the add-to-project workflow.

    Returns:
        JSON string containing both the verified search results and the final import result.
    """
    search_response = _resolve_fab_search(query, max_results, search_timeout_seconds)
    if not search_response.get("success"):
        return _tool_response_from_unreal(
            search_response,
            tool_name="search_and_add_free_fab_asset",
            success_message="Fab search and import completed.",
            error_code="FAB_SEARCH_AND_IMPORT_FAILED",
            default_error="Fab search failed before import could start.",
            extra_data=lambda result: {
                "query": query,
                "preferred_listing_id_or_url": preferred_listing_id_or_url,
                "preferred_title_substring": preferred_title_substring,
                "max_results": max_results,
                "search_timeout_seconds": search_timeout_seconds,
                "import_timeout_seconds": import_timeout_seconds,
                "search_result": result,
            },
        )

    results = _extract_fab_results(search_response)
    if not results:
        return _tool_error(
            "Fab search completed without any verified Add-to-Project results.",
            error_code="FAB_NO_RESULTS",
            data={
                "tool": "search_and_add_free_fab_asset",
                "query": query,
                "preferred_listing_id_or_url": preferred_listing_id_or_url,
                "preferred_title_substring": preferred_title_substring,
                "max_results": max_results,
                "search_timeout_seconds": search_timeout_seconds,
                "import_timeout_seconds": import_timeout_seconds,
                "search_result": search_response,
            },
        )

    selected_result, selection_reason = _select_fab_result(
        results,
        preferred_listing_id_or_url=preferred_listing_id_or_url,
        preferred_title_substring=preferred_title_substring
    )
    if selected_result is None:
        return _tool_error(
            "Unable to choose a Fab result to import from the verified search results.",
            error_code="FAB_SELECTION_FAILED",
            data={
                "tool": "search_and_add_free_fab_asset",
                "query": query,
                "preferred_listing_id_or_url": preferred_listing_id_or_url,
                "preferred_title_substring": preferred_title_substring,
                "max_results": max_results,
                "search_timeout_seconds": search_timeout_seconds,
                "import_timeout_seconds": import_timeout_seconds,
                "search_result": search_response,
            },
        )

    listing_target = selected_result.get("listing_id") or selected_result.get("listing_url")
    import_response = _resolve_fab_import(str(listing_target), import_timeout_seconds)

    combined_response = {
        "success": import_response.get("success", False),
        "status": import_response.get("status", "error"),
        "query": query,
        "selection_reason": selection_reason,
        "selected_result": selected_result,
        "search_result": search_response,
        "import_result": import_response
    }
    if import_response.get("import_path"):
        combined_response["import_path"] = import_response["import_path"]
    if not import_response.get("success"):
        combined_response["error"] = import_response.get("error", "Fab import failed")

    return _tool_response_from_unreal(
        combined_response,
        tool_name="search_and_add_free_fab_asset",
        success_message="Fab search and import completed.",
        error_code="FAB_SEARCH_AND_IMPORT_FAILED",
        default_error="Fab search and import failed.",
        extra_data=lambda result: {
            "query": query,
            "preferred_listing_id_or_url": preferred_listing_id_or_url,
            "preferred_title_substring": preferred_title_substring,
            "max_results": max_results,
            "search_timeout_seconds": search_timeout_seconds,
            "import_timeout_seconds": import_timeout_seconds,
            "selection_reason": result.get("selection_reason"),
            "selected_result": result.get("selected_result"),
            "search_result": result.get("search_result"),
            "import_result": result.get("import_result"),
            "import_path": result.get("import_path"),
        },
    )


# ===========================================================================
# Postmortem-driven MCP tools: level instances, landscape, viewport capture,
# project settings, and batch actor operations.
# ===========================================================================


@mcp.tool()
def create_level_from_template(level_path: str, template: str = "Basic") -> dict:
    """Create a new empty/basic level asset from an engine template.

    Args:
        level_path: Destination asset path, e.g. ``/Game/Maps/MyLevel``.
        template: ``Basic`` | ``Empty`` | an explicit ``/Engine/Maps/...`` path.
    """
    return _forward_structured_socket_response(
        send_to_unreal({"type": "create_level_from_template", "level_path": level_path, "template": template}),
        success_message=f"Created level {level_path}",
        error_code="LEVEL_OPERATION_FAILED",
        default_error="Failed to create level from template.",
    )


@mcp.tool()
def create_level_instance_from_selection(
    output_level_path: str,
    actor_names: list = None,
    pivot_mode: str = "CenterMinZ",
    external_actors: bool = True,
) -> dict:
    """Wrap the ``Level > Create Level Instance`` editor action.

    Args:
        output_level_path: Destination level asset (``/Game/Maps/...``).
        actor_names: Explicit labels/names, or ``None`` to use current selection.
        pivot_mode: Pivot placement strategy (``CenterMinZ`` / ``WorldOrigin`` etc.).
        external_actors: Whether to create the level with external actors enabled.
    """
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "create_level_instance_from_selection",
            "output_level_path": output_level_path,
            "actor_names": list(actor_names or []),
            "pivot_mode": pivot_mode,
            "external_actors": external_actors,
        }),
        success_message=f"Created level instance {output_level_path}",
        error_code="LEVEL_OPERATION_FAILED",
        default_error="Failed to create level instance from selection.",
    )


@mcp.tool()
def spawn_level_instance(
    level_asset_path: str,
    location: list = None,
    rotation: list = None,
    scale: list = None,
    runtime_behavior: str = "Embedded",
    actor_label: str = "",
) -> dict:
    """Spawn an ``ALevelInstance`` actor in the current world bound to a level asset."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "spawn_level_instance",
            "level_asset_path": level_asset_path,
            "location": list(location or [0, 0, 0]),
            "rotation": list(rotation or [0, 0, 0]),
            "scale": list(scale or [1, 1, 1]),
            "runtime_behavior": runtime_behavior,
            "actor_label": actor_label,
        }),
        success_message=f"Spawned level instance {level_asset_path}",
        error_code="LEVEL_OPERATION_FAILED",
        default_error="Failed to spawn level instance.",
    )


@mcp.tool()
def add_level_to_world(
    level_path: str,
    mode: str = "sublevel",
    location: list = None,
    rotation: list = None,
    streaming_class: str = "",
) -> dict:
    """Add a level asset to the current world.

    ``mode`` must be one of ``sublevel`` / ``level_instance`` / ``packed_level``.
    The crash-triggering ``LevelStreamingLevelInstanceEditor`` streaming class
    is refused outright; callers should use ``spawn_level_instance`` or
    ``create_level_instance_from_selection`` instead.
    """
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "add_level_to_world",
            "level_path": level_path,
            "mode": mode,
            "location": list(location or [0, 0, 0]),
            "rotation": list(rotation or [0, 0, 0]),
            "streaming_class": streaming_class,
        }),
        success_message=f"Added {level_path} to world.",
        error_code="LEVEL_OPERATION_FAILED",
        default_error="Failed to add level to world.",
    )


@mcp.tool()
def list_level_instances() -> dict:
    """Return every ``ALevelInstance`` actor in the current editor world."""
    return _forward_structured_socket_response(
        send_to_unreal({"type": "list_level_instances"}),
        success_message="Listed level instances.",
        error_code="LEVEL_OPERATION_FAILED",
        default_error="Failed to list level instances.",
    )


@mcp.tool()
def create_landscape(
    location: list = None,
    rotation: list = None,
    scale: list = None,
    size: list = None,
    material_path: str = "",
    actor_label: str = "MCP_Landscape",
) -> dict:
    """Spawn a Landscape actor at a given transform with an optional landscape material."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "create_landscape",
            "location": list(location or [0, 0, 0]),
            "rotation": list(rotation or [0, 0, 0]),
            "scale": list(scale) if scale else None,
            "size": list(size) if size else None,
            "material_path": material_path,
            "actor_label": actor_label,
        }),
        success_message="Spawned landscape.",
        error_code="LANDSCAPE_OPERATION_FAILED",
        default_error="Failed to create landscape.",
    )


@mcp.tool()
def set_landscape_material(actor_name: str, material_path: str) -> dict:
    """Assign ``material_path`` to the ``landscape_material`` slot on ``actor_name``."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "set_landscape_material",
            "actor_name": actor_name,
            "material_path": material_path,
        }),
        success_message=f"Set landscape material on {actor_name}.",
        error_code="LANDSCAPE_OPERATION_FAILED",
        default_error="Failed to set landscape material.",
    )


@mcp.tool()
def capture_editor_viewport(
    width: int = 1920,
    height: int = 1080,
    include_ui: bool = False,
    filename: str = "",
) -> dict:
    """Capture the Unreal editor viewport (not the whole desktop) as a PNG."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "capture_editor_viewport",
            "width": width,
            "height": height,
            "include_ui": include_ui,
            "filename": filename,
        }, timeout_seconds=60.0),
        success_message="Captured editor viewport.",
        error_code="VIEWPORT_CAPTURE_FAILED",
        default_error="Failed to capture editor viewport.",
    )


@mcp.tool()
def set_project_setting(settings_class: str, key: str, value, save_config: bool = True) -> dict:
    """Set a property on a project-settings class CDO (e.g. ``/Script/Engine.RendererSettings``)."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "set_project_setting",
            "settings_class": settings_class,
            "key": key,
            "value": value,
            "save_config": save_config,
        }),
        success_message=f"Set {settings_class}.{key}.",
        error_code="PROJECT_SETTING_FAILED",
        default_error="Failed to set project setting.",
    )


@mcp.tool()
def set_rendering_defaults(
    auto_exposure: bool = None,
    motion_blur: bool = None,
    bloom: bool = None,
    ambient_occlusion: bool = None,
    lens_flares: bool = None,
) -> dict:
    """Toggle common rendering defaults (``/Script/Engine.RendererSettings``)."""
    command = {"type": "set_rendering_defaults"}
    for name, value in [
        ("auto_exposure", auto_exposure),
        ("motion_blur", motion_blur),
        ("bloom", bloom),
        ("ambient_occlusion", ambient_occlusion),
        ("lens_flares", lens_flares),
    ]:
        if value is not None:
            command[name] = value
    return _forward_structured_socket_response(
        send_to_unreal(command),
        success_message="Rendering defaults applied.",
        error_code="PROJECT_SETTING_FAILED",
        default_error="Failed to apply rendering defaults.",
    )


@mcp.tool()
def duplicate_actors(actor_names: list, offset: list = None) -> dict:
    """Duplicate a set of actors with an optional world-space offset."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "duplicate_actors",
            "actor_names": list(actor_names or []),
            "offset": list(offset or [0, 0, 0]),
        }),
        success_message="Duplicated actors.",
        error_code="ACTOR_OPERATION_FAILED",
        default_error="Failed to duplicate actors.",
    )


@mcp.tool()
def replace_static_mesh(actor_names: list, mesh_path: str) -> dict:
    """Swap the static mesh asset on the given actors."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "replace_static_mesh",
            "actor_names": list(actor_names or []),
            "mesh_path": mesh_path,
        }),
        success_message="Replaced static mesh.",
        error_code="ACTOR_OPERATION_FAILED",
        default_error="Failed to replace static mesh.",
    )


@mcp.tool()
def replace_material(actor_names: list, material_path: str, slot_index: int = 0) -> dict:
    """Replace a material slot on the primary mesh component of each actor."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "replace_material",
            "actor_names": list(actor_names or []),
            "material_path": material_path,
            "slot_index": slot_index,
        }),
        success_message="Replaced material.",
        error_code="ACTOR_OPERATION_FAILED",
        default_error="Failed to replace material.",
    )


@mcp.tool()
def group_actors(actor_names: list, group_name: str) -> dict:
    """Group actors under a World Outliner folder path."""
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "group_actors",
            "actor_names": list(actor_names or []),
            "group_name": group_name,
        }),
        success_message=f"Grouped actors under {group_name}.",
        error_code="ACTOR_OPERATION_FAILED",
        default_error="Failed to group actors.",
    )


@mcp.tool()
def select_actors(query: str, match: str = "contains") -> dict:
    """Select actors in the outliner whose label/name match ``query``.

    ``match`` is one of ``contains`` (default), ``prefix``, or ``exact``.
    """
    return _forward_structured_socket_response(
        send_to_unreal({
            "type": "select_actors",
            "query": query,
            "match": match,
        }),
        success_message=f"Selected actors matching {query!r}.",
        error_code="ACTOR_OPERATION_FAILED",
        default_error="Failed to select actors.",
    )


if __name__ == "__main__":
    import traceback

    try:
        print("Server starting...", file=sys.stderr)
        print(f"Connecting to Unreal socket at {get_unreal_host()}:{get_unreal_port()}", file=sys.stderr)
        mcp.run()
    except Exception as e:
        print(f"Server crashed with error: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise
