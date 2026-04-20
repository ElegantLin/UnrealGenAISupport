import unreal
import traceback
from typing import Any


_LOG_HISTORY = []
_MAX_LOG_HISTORY = 2000


def _remember_log_line(message: str) -> None:
    _LOG_HISTORY.append(message)
    if len(_LOG_HISTORY) > _MAX_LOG_HISTORY:
        del _LOG_HISTORY[: len(_LOG_HISTORY) - _MAX_LOG_HISTORY]


def get_log_line_count() -> int:
    return len(_LOG_HISTORY)


def get_recent_unreal_logs(start_line: int = 0):
    safe_start = max(int(start_line or 0), 0)
    return list(_LOG_HISTORY[safe_start:])


def begin_command_log_snapshot():
    return get_log_line_count()


def end_command_log_snapshot(start_line):
    return get_recent_unreal_logs(start_line)


def log_info(message: str) -> None:
    """
    Log an informational message to the Unreal log
    
    Args:
        message: The message to log
    """
    formatted_message = f"[AI Plugin] {message}"
    unreal.log(formatted_message)
    _remember_log_line(formatted_message)


def log_warning(message: str) -> None:
    """
    Log a warning message to the Unreal log
    
    Args:
        message: The message to log
    """
    formatted_message = f"[AI Plugin] WARNING: {message}"
    unreal.log_warning(formatted_message)
    _remember_log_line(formatted_message)


def log_error(message: str, include_traceback: bool = False) -> None:
    """
    Log an error message to the Unreal log
    
    Args:
        message: The message to log
        include_traceback: Whether to include the traceback in the log
    """
    error_message = f"[AI Plugin] ERROR: {message}"
    unreal.log_error(error_message)
    _remember_log_line(error_message)

    if include_traceback:
        tb = traceback.format_exc()
        traceback_message = f"[AI Plugin] Traceback:\n{tb}"
        unreal.log_error(traceback_message)
        _remember_log_line(traceback_message)


def log_command(command_type: str, details: Any = None) -> None:
    """
    Log a command being processed
    
    Args:
        command_type: The type of command being processed
        details: Optional details about the command
    """
    if details:
        formatted_message = f"[AI Plugin] Processing {command_type} command: {details}"
    else:
        formatted_message = f"[AI Plugin] Processing {command_type} command"
    unreal.log(formatted_message)
    _remember_log_line(formatted_message)


def log_result(command_type: str, success: bool, details: Any = None) -> None:
    """
    Log the result of a command
    
    Args:
        command_type: The type of command that was processed
        success: Whether the command was successful
        details: Optional details about the result
    """
    status = "successful" if success else "failed"

    if details:
        formatted_message = f"[AI Plugin] {command_type} command {status}: {details}"
    else:
        formatted_message = f"[AI Plugin] {command_type} command {status}"
    unreal.log(formatted_message)
    _remember_log_line(formatted_message)
