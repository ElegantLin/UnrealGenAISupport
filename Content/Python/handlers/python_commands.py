import textwrap

import unreal
import sys
from typing import Any, Dict, List
import os
import uuid
import time
import traceback

# Assuming a logging module similar to your example
from utils import logging as log

try:
    from utils.mcp_response import err, ok
    from utils.safety import classify_script, summarize_dirty_packages
except ImportError:  # pragma: no cover - alt import path under pytest
    from Content.Python.utils.mcp_response import err, ok
    from Content.Python.utils.safety import classify_script, summarize_dirty_packages


def execute_script(script_file, output_file, error_file, status_file):
    """Execute a Python script with output and error redirection."""
    with open(output_file, 'w') as output_file_handle, open(error_file, 'w') as error_file_handle:
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        sys.stdout = output_file_handle
        sys.stderr = error_file_handle

        success = True
        try:
            with open(script_file, 'r') as f:
                exec(f.read())
        except Exception as e:
            traceback.print_exc()
            success = False
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

        with open(status_file, 'w') as f:
            f.write('1' if success else '0')


def get_log_line_count():
    """
    Get the current line count of the Unreal log file
    """
    try:
        log_path = os.path.join(unreal.Paths.project_log_dir(), "Unreal.log")
        if os.path.exists(log_path):
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                return sum(1 for _ in f)
        return 0
    except Exception as e:
        log.log_error(f"Error getting log line count: {str(e)}")
        return 0


def get_recent_unreal_logs(start_line=None):
    """
    Retrieve recent Unreal Engine log entries to provide context for errors
    
    Args:
        start_line: Optional line number to start from (to only get new logs)
        
    Returns:
        String containing log entries or None if logs couldn't be accessed
    """
    try:
        log_path = os.path.join(unreal.Paths.project_log_dir(), "Unreal.log")
        if os.path.exists(log_path):
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                if start_line is None:
                    # Legacy behavior - get last 20 lines
                    lines = f.readlines()
                    return "".join(lines[-20:])
                else:
                    # Skip to the starting line
                    for i, _ in enumerate(f):
                        if i >= start_line - 1:
                            break
                    
                    # Get all new lines
                    new_lines = f.readlines()
                    return "".join(new_lines) if new_lines else "No new log entries generated"
        return None
    except Exception as e:
        log.log_error(f"Error getting recent logs: {str(e)}")
        return None


def _snapshot_dirty_packages() -> List[str]:
    try:
        editor_loading = getattr(unreal, "EditorLoadingAndSavingUtils", None)
        if editor_loading is None:
            return []
        getter = getattr(editor_loading, "get_dirty_content_packages", None) or getattr(
            editor_loading, "get_dirty_packages", None
        )
        if not callable(getter):
            return []
        packages = getter() or []
        return summarize_dirty_packages(packages)
    except Exception:
        return []


def _diff_changed_packages(before: List[str], after: List[str]) -> List[str]:
    before_set = set(before or [])
    return [pkg for pkg in (after or []) if pkg not in before_set]


def handle_execute_python(command: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle a command to execute a Python script in Unreal Engine.

    The script tag is treated as ``unsafe`` -- callers should prefer the
    semantic MCP tools when possible.

    Args:
        command: The command dictionary containing:
            - script: The Python code to execute as a string
            - force: Optional boolean to bypass destructive-script checks
            - read_only: When true, refuse scripts classified as destructive
              and skip execution that would mutate disk
            - dry_run: When true, return the classification without executing
    """
    script_file = output_file = error_file = status_file = None
    try:
        script = command.get("script")
        force = bool(command.get("force", False))
        read_only = bool(command.get("read_only", False))
        dry_run = bool(command.get("dry_run", False))

        if not script:
            log.log_error("Missing required parameter for execute_python: script")
            return err(
                "Missing required parameter: script",
                error_code="SCRIPT_REQUIRED",
                data={"unsafe": True},
            )

        log.log_command("execute_python", f"Script: {script[:50]}...")

        classification = classify_script(script)
        warnings: List[str] = ["execute_python is an unsafe command; prefer semantic MCP tools when possible."]

        if read_only and classification.is_destructive:
            return err(
                "Script is classified as destructive but read_only=True was requested.",
                error_code="UNSAFE_COMMAND_REQUIRED",
                data={
                    "classification": classification.to_dict(),
                    "unsafe": True,
                },
                warnings=warnings,
            )

        if classification.requires_force and not force:
            log.log_warning("Potentially destructive script detected")
            return err(
                (
                    "This script may involve destructive actions (e.g., deleting or saving files) "
                    "not explicitly requested. Please confirm with 'Yes, execute it' or set force=True."
                ),
                error_code="UNSAFE_COMMAND_REQUIRED",
                data={
                    "classification": classification.to_dict(),
                    "unsafe": True,
                },
                warnings=warnings,
            )

        if dry_run:
            return ok(
                "Dry run complete; script not executed.",
                data={
                    "classification": classification.to_dict(),
                    "unsafe": True,
                    "executed": False,
                },
                warnings=warnings,
            )

        log_start_line = log.get_log_line_count()
        dirty_before = _snapshot_dirty_packages()

        temp_dir = os.path.join(unreal.Paths.project_saved_dir(), "Temp", "PythonExec")
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)

        script_file = os.path.join(temp_dir, f"script_{uuid.uuid4().hex}.py")
        output_file = os.path.join(temp_dir, "output.txt")
        error_file = os.path.join(temp_dir, "error.txt")
        status_file = os.path.join(temp_dir, "status.txt")

        dedented_script = textwrap.dedent(script).strip()
        with open(script_file, 'w') as f:
            f.write(dedented_script)

        execute_script(script_file, output_file, error_file, status_file)
        time.sleep(0.5)

        output = ""
        error_text = ""
        success = False

        if os.path.exists(output_file):
            with open(output_file, 'r') as f:
                output = f.read()
        if os.path.exists(error_file):
            with open(error_file, 'r') as f:
                error_text = f.read()
        if os.path.exists(status_file):
            with open(status_file, 'r') as f:
                success = f.read().strip() == "1"

        for path in [script_file, output_file, error_file, status_file]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

        recent_logs = log.get_recent_unreal_logs(log_start_line)
        dirty_after = _snapshot_dirty_packages()
        changed_assets = _diff_changed_packages(dirty_before, dirty_after)

        if not success and error_text:
            if "set_world_location() required argument 'sweep'" in error_text:
                error_text += "\n\nHINT: set_world_location() requires sweep=False."
            elif "set_world_location() required argument 'teleport'" in error_text:
                error_text += "\n\nHINT: set_world_location() requires teleport=False."
            elif "set_actor_location() required argument 'teleport'" in error_text:
                error_text += "\n\nHINT: set_actor_location() requires teleport=False."

        data = {
            "executed": True,
            "unsafe": True,
            "classification": classification.to_dict(),
            "output": output,
            "recent_logs": recent_logs,
            "dirty_packages": dirty_after,
            "changed_assets": changed_assets,
        }

        if success:
            log.log_result("execute_python", True, "Script executed.")
            return ok("Script executed.", data=data, warnings=warnings)

        data["error"] = error_text or "Execution failed without specific error"
        log.log_error(f"Script execution failed: {error_text}")
        return err(
            data["error"],
            error_code="EXECUTE_PYTHON_FAILED",
            data=data,
            warnings=warnings,
        )

    except Exception as exc:
        log.log_error(f"Error handling execute_python: {exc}", include_traceback=True)
        return err(
            str(exc),
            error_code="EXECUTE_PYTHON_FAILED",
            data={"unsafe": True},
        )
    finally:
        for path in [script_file, output_file, error_file, status_file]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


def handle_execute_unreal_command(command: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle a command to execute an Unreal Engine console command.

    Args:
        command: The command dictionary containing:
            - command: The Unreal Engine console command to execute
            - force: Optional boolean to bypass safety checks (default: False)
    """
    try:
        cmd = command.get("command")
        force = bool(command.get("force", False))

        if not cmd:
            log.log_error("Missing required parameter for execute_unreal_command: command")
            return err(
                "Missing required parameter: command",
                error_code="COMMAND_REQUIRED",
                data={"unsafe": True},
            )

        if cmd.strip().lower().startswith("py "):
            log.log_error("Attempted to run a Python script with execute_unreal_command")
            return err(
                (
                    "Use 'execute_python' to run Python scripts instead of "
                    "'execute_unreal_command' with the 'py' prefix."
                ),
                error_code="WRONG_COMMAND_TYPE",
                data={"unsafe": True},
            )

        log.log_command("execute_unreal_command", f"Command: {cmd}")
        log_start_line = log.get_log_line_count()

        destructive_keywords = ["delete", "save", "quit", "exit", "restart"]
        is_destructive = any(keyword in cmd.lower() for keyword in destructive_keywords)
        warnings: List[str] = ["execute_unreal_command is an unsafe command."]

        if is_destructive and not force:
            log.log_warning("Potentially destructive command detected")
            return err(
                (
                    "This command may involve destructive actions. "
                    "Confirm with 'Yes, execute it' or set force=True."
                ),
                error_code="UNSAFE_COMMAND_REQUIRED",
                data={"unsafe": True},
                warnings=warnings,
            )

        world = unreal.EditorLevelLibrary.get_editor_world()
        unreal.SystemLibrary.execute_console_command(world, cmd)
        time.sleep(1.0)

        recent_logs = log.get_recent_unreal_logs(log_start_line)

        log.log_result("execute_unreal_command", True, f"Command '{cmd}' executed")
        return ok(
            f"Command '{cmd}' executed.",
            data={"command": cmd, "recent_logs": recent_logs, "unsafe": True},
            warnings=warnings,
        )

    except Exception as exc:
        log.log_error(f"Error handling execute_unreal_command: {exc}", include_traceback=True)
        recent_logs = log.get_recent_unreal_logs(0)
        return err(
            str(exc),
            error_code="EXECUTE_UNREAL_COMMAND_FAILED",
            data={"recent_logs": recent_logs, "unsafe": True},
        )
