"""Safe editor-viewport capture (postmortem gap #6).

The existing ``take_screenshot`` tool uses ``mss`` to capture the primary
monitor, which can leak desktop contents.  This handler captures *only* the
Unreal editor viewport via ``AutomationLibrary.take_high_res_screenshot``
(or the ``HighResShot`` console command fallback) and returns the PNG
contents as a base64 string.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any, Dict, Optional

try:
    from utils.mcp_response import err, ok
except ImportError:  # pragma: no cover
    from Content.Python.utils.mcp_response import err, ok


def _get_unreal_module():
    try:
        import unreal  # type: ignore
    except ImportError:
        return None
    return unreal


def _unavailable(message: str) -> Dict[str, Any]:
    return err(error_code="UNAVAILABLE_OUTSIDE_EDITOR", message=message)


def _project_saved_dir(unreal_mod) -> str:
    paths = getattr(unreal_mod, "Paths", None)
    if paths is None:
        return ""
    try:
        return str(paths.project_saved_dir())
    except Exception:
        return ""


def _screenshot_dir(unreal_mod) -> str:
    paths = getattr(unreal_mod, "Paths", None)
    for getter in ("screen_shot_dir", "project_saved_dir"):
        fn = getattr(paths, getter, None) if paths is not None else None
        if callable(fn):
            try:
                candidate = str(fn())
                if candidate:
                    if getter == "project_saved_dir":
                        candidate = os.path.join(candidate, "Screenshots")
                    return candidate
            except Exception:
                continue
    return ""


def _wait_for_file(path: str, timeout: float = 10.0, poll: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.isfile(path):
            try:
                if os.path.getsize(path) > 0:
                    return True
            except OSError:
                pass
        time.sleep(poll)
    return False


def handle_capture_editor_viewport(command: Dict[str, Any]) -> Dict[str, Any]:
    """Take a high-res screenshot of the active editor viewport.

    ``width`` / ``height`` override the automation capture resolution.  The
    resulting PNG is returned as base64 alongside its on-disk path so callers
    can verify without round-tripping through another tool.
    """

    unreal_mod = _get_unreal_module()
    if unreal_mod is None:
        return _unavailable("capture_editor_viewport requires a running Unreal Editor.")

    width = command.get("width")
    height = command.get("height")
    include_ui = bool(command.get("include_ui", False))
    filename = str(command.get("filename") or "").strip()

    if not filename:
        filename = f"MCP_ViewportCapture_{int(time.time() * 1000)}.png"
    elif not filename.lower().endswith(".png"):
        filename += ".png"

    out_dir = _screenshot_dir(unreal_mod) or os.path.join(_project_saved_dir(unreal_mod), "Screenshots")
    if not out_dir:
        return err(
            error_code="VIEWPORT_CAPTURE_FAILED",
            message="Could not resolve a project Saved/Screenshots directory.",
        )

    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as exc:  # pragma: no cover
        return err(error_code="VIEWPORT_CAPTURE_FAILED", message=f"Failed to create screenshot dir: {exc}")

    out_path = os.path.join(out_dir, filename)

    # Prefer AutomationLibrary for a proper per-viewport capture.
    automation = getattr(unreal_mod, "AutomationLibrary", None)
    console = getattr(unreal_mod, "SystemLibrary", None)

    took_shot = False
    error_hint: Optional[str] = None

    if automation is not None and hasattr(automation, "take_high_res_screenshot"):
        try:
            w = int(width) if width else 1920
            h = int(height) if height else 1080
            # Signature: take_high_res_screenshot(res_x, res_y, filename, ...)
            automation.take_high_res_screenshot(w, h, out_path, None, False, False, None, include_ui)
            took_shot = True
        except TypeError:
            try:
                automation.take_high_res_screenshot(w, h, out_path)
                took_shot = True
            except Exception as exc:  # pragma: no cover
                error_hint = str(exc)
        except Exception as exc:  # pragma: no cover
            error_hint = str(exc)

    if not took_shot and console is not None and hasattr(console, "execute_console_command"):
        cmd = f'HighResShot filename="{out_path}"'
        if width and height:
            cmd = f"HighResShot {int(width)}x{int(height)} filename=\"{out_path}\""
        try:
            console.execute_console_command(None, cmd)
            took_shot = True
        except Exception as exc:  # pragma: no cover
            error_hint = str(exc)

    if not took_shot:
        return err(
            error_code="VIEWPORT_CAPTURE_FAILED",
            message=error_hint or "No supported viewport capture API is available.",
        )

    if not _wait_for_file(out_path, timeout=float(command.get("timeout", 10.0))):
        return err(
            error_code="VIEWPORT_CAPTURE_FAILED",
            message=f"Viewport capture did not produce a file at {out_path} in time.",
            data={"expected_path": out_path},
        )

    try:
        with open(out_path, "rb") as fh:
            encoded = base64.b64encode(fh.read()).decode("ascii")
    except OSError as exc:
        return err(error_code="VIEWPORT_CAPTURE_FAILED", message=f"Could not read screenshot file: {exc}")

    return ok(
        "Captured editor viewport.",
        data={
            "path": out_path,
            "image_base64": encoded,
            "format": "png",
            "include_ui": include_ui,
        },
    )
