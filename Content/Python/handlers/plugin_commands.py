import glob
import json
import os
import platform
import time
from typing import Any, Dict, List, Optional

import unreal

from utils import logging as log


_PENDING_EDITOR_EXIT: Optional[Dict[str, Any]] = None
_PENDING_EDITOR_EXIT_HANDLE = None


def _normalize_path(path: str) -> str:
    if not path:
        return ""
    return os.path.abspath(os.path.normpath(path))


def _get_project_dir() -> str:
    try:
        return _normalize_path(unreal.Paths.project_dir())
    except Exception:
        return ""


def _resolve_project_file_path() -> str:
    getter_names = ("get_project_file_path", "project_file_path")

    for getter_name in getter_names:
        getter = getattr(unreal.Paths, getter_name, None)
        if callable(getter):
            try:
                candidate = _normalize_path(getter())
                if candidate and os.path.isfile(candidate):
                    return candidate
            except Exception:
                continue

    project_dir = _get_project_dir()
    if not project_dir:
        return ""

    candidates = sorted(glob.glob(os.path.join(project_dir, "*.uproject")))
    if len(candidates) == 1:
        return _normalize_path(candidates[0])

    project_name = ""
    get_project_name = getattr(unreal.SystemLibrary, "get_project_name", None)
    if callable(get_project_name):
        try:
            project_name = str(get_project_name()).strip()
        except Exception:
            project_name = ""

    if project_name:
        preferred = _normalize_path(os.path.join(project_dir, f"{project_name}.uproject"))
        if preferred in [_normalize_path(candidate) for candidate in candidates]:
            return preferred

    return _normalize_path(candidates[0]) if candidates else ""


def _resolve_engine_dir() -> str:
    try:
        return _normalize_path(unreal.Paths.engine_dir())
    except Exception:
        return ""


def _build_editor_path_candidates(engine_dir: str) -> List[str]:
    if not engine_dir:
        return []

    system_name = platform.system().lower()
    candidates: List[str] = []

    if system_name == "windows":
        candidates.extend([
            os.path.join(engine_dir, "Binaries", "Win64", "UnrealEditor.exe"),
            os.path.join(engine_dir, "Binaries", "Win64", "UE4Editor.exe"),
        ])
    elif system_name == "darwin":
        candidates.extend([
            os.path.join(engine_dir, "Binaries", "Mac", "UnrealEditor.app", "Contents", "MacOS", "UnrealEditor"),
            os.path.join(engine_dir, "Binaries", "Mac", "UE4Editor.app", "Contents", "MacOS", "UE4Editor"),
            os.path.join(engine_dir, "Binaries", "Mac", "UnrealEditor"),
            os.path.join(engine_dir, "Binaries", "Mac", "UE4Editor"),
        ])
    else:
        candidates.extend([
            os.path.join(engine_dir, "Binaries", "Linux", "UnrealEditor"),
            os.path.join(engine_dir, "Binaries", "Linux", "UE4Editor"),
        ])

    fallback_patterns = [
        os.path.join(engine_dir, "Binaries", "**", "UnrealEditor"),
        os.path.join(engine_dir, "Binaries", "**", "UnrealEditor.exe"),
        os.path.join(engine_dir, "Binaries", "**", "UE4Editor"),
        os.path.join(engine_dir, "Binaries", "**", "UE4Editor.exe"),
    ]
    for pattern in fallback_patterns:
        candidates.extend(sorted(glob.glob(pattern, recursive=True)))

    normalized_candidates: List[str] = []
    seen = set()
    for candidate in candidates:
        normalized = _normalize_path(candidate)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_candidates.append(normalized)

    return normalized_candidates


def _resolve_editor_path(engine_dir: str) -> str:
    for candidate in _build_editor_path_candidates(engine_dir):
        if os.path.isfile(candidate):
            return candidate
    return ""


def _get_dirty_package_names() -> List[str]:
    dirty_package_names: List[str] = []
    seen = set()

    loading_utils = getattr(unreal, "EditorLoadingAndSavingUtils", None)
    if loading_utils is None:
        return dirty_package_names

    getter_names = ("get_dirty_content_packages", "get_dirty_map_packages")
    for getter_name in getter_names:
        getter = getattr(loading_utils, getter_name, None)
        if not callable(getter):
            continue

        try:
            for package in getter():
                package_name = package.get_name() if hasattr(package, "get_name") else str(package)
                if package_name and package_name not in seen:
                    seen.add(package_name)
                    dirty_package_names.append(package_name)
        except Exception as exc:
            log.log_warning(f"Failed to inspect dirty packages via {getter_name}: {exc}")

    return dirty_package_names


def _normalize_architecture_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"

    normalized_key = text.casefold().replace("-", "").replace("_", "")
    if normalized_key in ("amd64", "win64", "x64", "x8664"):
        return "x64"
    if normalized_key in ("arm64", "aarch64"):
        return "arm64"
    return text


def _guess_architecture_from_text(value: Any) -> str:
    text = str(value or "").casefold()
    if not text:
        return "unknown"

    if "arm64" in text or "aarch64" in text:
        return "arm64"
    if "x86_64" in text or "amd64" in text or "win64" in text or "x64" in text:
        return "x64"
    return "unknown"


def _resolve_editor_binary_architecture(editor_path: str) -> str:
    for candidate in (editor_path, platform.machine()):
        architecture = _guess_architecture_from_text(candidate)
        if architecture != "unknown":
            return architecture
    return "unknown"


def _resolve_project_target_architecture(descriptor: Dict[str, Any]) -> str:
    candidate_values: List[str] = []

    for key in (
        "TargetArchitecture",
        "Architecture",
        "ProjectTargetArchitecture",
        "TargetArchitectureOverride",
    ):
        value = descriptor.get(key)
        if isinstance(value, str) and value.strip():
            candidate_values.append(value)

    for key in ("TargetPlatforms", "PlatformAllowList", "SupportedTargetPlatforms"):
        values = descriptor.get(key)
        if isinstance(values, (list, tuple)):
            candidate_values.extend(str(item) for item in values if str(item).strip())

    modules = descriptor.get("Modules")
    if isinstance(modules, list):
        for module in modules:
            if not isinstance(module, dict):
                continue
            for key in ("TargetArchitecture", "Architecture", "PlatformAllowList"):
                value = module.get(key)
                if isinstance(value, str) and value.strip():
                    candidate_values.append(value)
                elif isinstance(value, (list, tuple)):
                    candidate_values.extend(str(item) for item in value if str(item).strip())

    for candidate in candidate_values:
        architecture = _guess_architecture_from_text(candidate)
        if architecture != "unknown":
            return architecture

    return "unknown"


def _collect_binary_candidates(project_dir: str) -> List[str]:
    if not project_dir:
        return []

    patterns = [
        os.path.join(project_dir, "Binaries", "**", "*"),
        os.path.join(project_dir, "Plugins", "**", "Binaries", "**", "*"),
    ]
    extensions = {".dll", ".dylib", ".so", ".bundle"}
    candidates: List[str] = []
    seen = set()

    for pattern in patterns:
        for candidate in glob.glob(pattern, recursive=True):
            if not os.path.isfile(candidate):
                continue
            if os.path.splitext(candidate)[1].lower() not in extensions:
                continue

            normalized = _normalize_path(candidate)
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(normalized)

    return candidates


def _resolve_module_architectures(project_dir: str, editor_binary_architecture: str) -> List[str]:
    architectures: List[str] = []
    seen = set()
    binary_candidates = _collect_binary_candidates(project_dir)

    for candidate in binary_candidates:
        architecture = _guess_architecture_from_text(candidate)
        if architecture == "unknown":
            continue
        if architecture in seen:
            continue
        seen.add(architecture)
        architectures.append(architecture)

    if architectures:
        return architectures

    if binary_candidates and editor_binary_architecture != "unknown":
        return [editor_binary_architecture]

    return []


def _get_enabled_plugin_names(descriptor: Dict[str, Any]) -> List[str]:
    enabled_plugin_names: List[str] = []
    seen = set()

    for entry in descriptor.get("Plugins", []):
        if not isinstance(entry, dict):
            continue

        plugin_name = str(entry.get("Name", "")).strip()
        if not plugin_name or not bool(entry.get("Enabled", True)):
            continue
        if plugin_name.casefold() in seen:
            continue

        seen.add(plugin_name.casefold())
        enabled_plugin_names.append(plugin_name)

    enabled_plugin_names.sort()
    return enabled_plugin_names


def _resolve_input_system(enabled_plugins: List[str]) -> str:
    enabled_lookup = {plugin.casefold() for plugin in enabled_plugins}
    if "enhancedinput" in enabled_lookup:
        return "EnhancedInput"
    if "commoninput" in enabled_lookup:
        return "CommonInput"
    return "unknown"


def _extract_asset_path(asset: Any) -> str:
    if asset is None:
        return ""

    for getter_name in ("get_path_name", "get_name"):
        getter = getattr(asset, getter_name, None)
        if callable(getter):
            try:
                value = str(getter()).strip()
                if value:
                    return value
            except Exception:
                continue

    return str(asset).strip()


def _get_open_asset_paths() -> List[str]:
    open_asset_paths: List[str] = []
    seen = set()

    get_editor_subsystem = getattr(unreal, "get_editor_subsystem", None)
    asset_editor_subsystem_class = getattr(unreal, "AssetEditorSubsystem", None)
    if not callable(get_editor_subsystem) or asset_editor_subsystem_class is None:
        return open_asset_paths

    try:
        asset_editor_subsystem = get_editor_subsystem(asset_editor_subsystem_class)
    except Exception:
        return open_asset_paths

    for getter_name in ("get_all_open_assets", "get_open_assets"):
        getter = getattr(asset_editor_subsystem, getter_name, None)
        if not callable(getter):
            continue

        try:
            assets = getter()
        except Exception as exc:
            log.log_warning(f"Failed to inspect open assets via {getter_name}: {exc}")
            continue

        for asset in assets or []:
            asset_path = _extract_asset_path(asset)
            if not asset_path or asset_path in seen:
                continue
            seen.add(asset_path)
            open_asset_paths.append(asset_path)

        if open_asset_paths:
            break

    return open_asset_paths


def _load_project_descriptor() -> Dict[str, Any]:
    project_file_path = _resolve_project_file_path()
    if not project_file_path:
        raise RuntimeError("Could not resolve the current .uproject path.")

    with open(project_file_path, "r", encoding="utf-8") as descriptor_file:
        descriptor = json.load(descriptor_file)

    if not isinstance(descriptor, dict):
        raise RuntimeError("The .uproject file did not contain a valid JSON object.")

    plugins = descriptor.get("Plugins")
    if plugins is None:
        descriptor["Plugins"] = []
    elif not isinstance(plugins, list):
        raise RuntimeError("The .uproject file contains an invalid Plugins section.")

    return descriptor


def _write_project_descriptor(project_file_path: str, descriptor: Dict[str, Any]) -> None:
    serialized = json.dumps(descriptor, indent="\t", ensure_ascii=False)
    with open(project_file_path, "w", encoding="utf-8") as descriptor_file:
        descriptor_file.write(serialized)
        descriptor_file.write("\n")


def _schedule_editor_exit(delay_seconds: float = 1.5) -> float:
    global _PENDING_EDITOR_EXIT
    global _PENDING_EDITOR_EXIT_HANDLE

    execute_at = time.time() + max(delay_seconds, 0.1)
    _PENDING_EDITOR_EXIT = {"execute_at": execute_at}

    if _PENDING_EDITOR_EXIT_HANDLE is None:
        _PENDING_EDITOR_EXIT_HANDLE = unreal.register_slate_post_tick_callback(_run_pending_editor_exit)

    return execute_at


def _run_pending_editor_exit(delta_time: float) -> None:
    del delta_time

    global _PENDING_EDITOR_EXIT
    global _PENDING_EDITOR_EXIT_HANDLE

    if not _PENDING_EDITOR_EXIT:
        return

    if time.time() < _PENDING_EDITOR_EXIT["execute_at"]:
        return

    _PENDING_EDITOR_EXIT = None

    if _PENDING_EDITOR_EXIT_HANDLE is not None and hasattr(unreal, "unregister_slate_post_tick_callback"):
        try:
            unreal.unregister_slate_post_tick_callback(_PENDING_EDITOR_EXIT_HANDLE)
        except Exception:
            pass
        _PENDING_EDITOR_EXIT_HANDLE = None

    try:
        _request_editor_exit_now()
    except Exception as exc:
        log.log_error(f"Failed to quit Unreal Editor: {exc}", include_traceback=True)


def _request_editor_exit_now() -> None:
    world = None
    try:
        world = unreal.EditorLevelLibrary.get_editor_world()
    except Exception:
        world = None

    quit_editor = getattr(unreal.SystemLibrary, "quit_editor", None)
    if callable(quit_editor):
        try:
            quit_editor()
            return
        except TypeError:
            try:
                quit_editor(world)
                return
            except Exception:
                pass
        except Exception:
            pass

    last_error: Optional[Exception] = None
    for quit_command in ("QUIT_EDITOR", "QUIT", "EXIT"):
        try:
            unreal.SystemLibrary.execute_console_command(world, quit_command)
            return
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise RuntimeError(f"No supported editor quit mechanism succeeded: {last_error}") from last_error

    raise RuntimeError("No supported editor quit mechanism is available in this Unreal Python environment.")


def handle_get_editor_context(command: Dict[str, Any]) -> Dict[str, Any]:
    del command

    try:
        project_file_path = _resolve_project_file_path()
        project_dir = _get_project_dir()
        engine_dir = _resolve_engine_dir()
        editor_path = _resolve_editor_path(engine_dir)
        dirty_packages = _get_dirty_package_names()
        dirty_assets = list(dirty_packages)
        open_asset_paths = _get_open_asset_paths()

        descriptor: Dict[str, Any] = {"Plugins": []}
        try:
            if project_file_path:
                descriptor = _load_project_descriptor()
        except Exception as exc:
            log.log_warning(f"Failed to inspect project descriptor for editor context: {exc}")

        enabled_plugins = _get_enabled_plugin_names(descriptor)
        editor_binary_architecture = _resolve_editor_binary_architecture(editor_path)
        project_target_architecture = _resolve_project_target_architecture(descriptor)
        module_architectures = _resolve_module_architectures(project_dir, editor_binary_architecture)
        input_system = _resolve_input_system(enabled_plugins)

        response = {
            "success": bool(project_file_path),
            "project_file_path": project_file_path,
            "project_dir": project_dir,
            "engine_dir": engine_dir,
            "editor_path": editor_path,
            "editor_path_candidates": _build_editor_path_candidates(engine_dir),
            "editor_pid": os.getpid(),
            "platform": platform.system(),
            "dirty_packages": dirty_packages,
            "dirty_package_count": len(dirty_packages),
            "editor_binary_architecture": editor_binary_architecture,
            "project_target_architecture": project_target_architecture,
            "module_architectures": module_architectures,
            "input_system": input_system,
            "enabled_plugins": enabled_plugins,
            "dirty_assets": dirty_assets,
            "open_asset_paths": open_asset_paths,
        }

        if not project_file_path:
            response["error"] = "Could not resolve the current .uproject path."

        return response
    except Exception as exc:
        log.log_error(f"Error resolving editor context: {exc}", include_traceback=True)
        return {"success": False, "error": str(exc)}


def handle_set_plugin_enabled(command: Dict[str, Any]) -> Dict[str, Any]:
    try:
        plugin_name = str(command.get("plugin_name", "")).strip()
        enabled = bool(command.get("enabled", True))
        target_allow_list = command.get("target_allow_list")

        if not plugin_name:
            return {"success": False, "error": "Missing required parameter: plugin_name"}

        descriptor = _load_project_descriptor()
        project_file_path = _resolve_project_file_path()
        plugins = descriptor.setdefault("Plugins", [])

        normalized_allow_list = None
        if target_allow_list is not None:
            if isinstance(target_allow_list, str):
                normalized_allow_list = [target_allow_list]
            elif isinstance(target_allow_list, (list, tuple)):
                normalized_allow_list = [str(item) for item in target_allow_list if str(item).strip()]
            else:
                return {"success": False, "error": "target_allow_list must be a list of strings when provided"}

        if not isinstance(plugins, list):
            return {"success": False, "error": "Invalid Plugins section in .uproject"}

        changed = False
        action = "updated"
        plugin_entry = None

        for entry in plugins:
            if isinstance(entry, dict) and str(entry.get("Name", "")).casefold() == plugin_name.casefold():
                plugin_entry = entry
                break

        if plugin_entry is None:
            plugin_entry = {
                "Name": plugin_name,
                "Enabled": enabled,
            }
            if normalized_allow_list:
                plugin_entry["TargetAllowList"] = normalized_allow_list
            plugins.append(plugin_entry)
            changed = True
            action = "added"
        else:
            if plugin_entry.get("Enabled") != enabled:
                plugin_entry["Enabled"] = enabled
                changed = True

            if normalized_allow_list is not None:
                if normalized_allow_list:
                    if plugin_entry.get("TargetAllowList") != normalized_allow_list:
                        plugin_entry["TargetAllowList"] = normalized_allow_list
                        changed = True
                elif "TargetAllowList" in plugin_entry:
                    plugin_entry.pop("TargetAllowList", None)
                    changed = True

        if changed:
            _write_project_descriptor(project_file_path, descriptor)

        restart_required = changed and enabled
        state_text = "enabled" if enabled else "disabled"
        message = (
            f"Plugin '{plugin_name}' was {action} in {os.path.basename(project_file_path)} and marked as {state_text}."
            if changed
            else f"Plugin '{plugin_name}' is already {state_text} in {os.path.basename(project_file_path)}."
        )
        if restart_required:
            message += " Restart Unreal Editor to load the plugin."

        log.log_result("set_plugin_enabled", True, message)
        return {
            "success": True,
            "plugin_name": plugin_name,
            "enabled": enabled,
            "changed": changed,
            "restart_required": restart_required,
            "project_file_path": project_file_path,
            "message": message,
        }
    except Exception as exc:
        log.log_error(f"Error updating plugin state: {exc}", include_traceback=True)
        return {"success": False, "error": str(exc)}


def handle_request_editor_restart(command: Dict[str, Any]) -> Dict[str, Any]:
    try:
        force = bool(command.get("force", False))
        delay_seconds = float(command.get("delay_seconds", 1.5))
        dirty_packages = _get_dirty_package_names()

        if dirty_packages and not force:
            return {
                "success": False,
                "confirmation_required": True,
                "reason": "unsaved_changes",
                "message": (
                    "Unreal Editor has unsaved assets or maps. Ask the user to confirm whether to restart without saving."
                ),
                "error": (
                    "Unreal Editor has unsaved assets or maps. Re-run with force=True to restart without saving."
                ),
                "dirty_packages": dirty_packages,
                "dirty_package_count": len(dirty_packages),
                "suggested_retry": "restart_editor(force=True)",
            }

        scheduled_at = _schedule_editor_exit(delay_seconds)
        log.log_result(
            "request_editor_restart",
            True,
            f"Editor restart exit scheduled in {max(delay_seconds, 0.1):.1f}s",
        )
        return {
            "success": True,
            "message": "Editor exit scheduled; the external launcher can now reopen the project.",
            "dirty_packages": dirty_packages,
            "dirty_package_count": len(dirty_packages),
            "delay_seconds": max(delay_seconds, 0.1),
            "scheduled_at": scheduled_at,
        }
    except Exception as exc:
        log.log_error(f"Error scheduling editor restart: {exc}", include_traceback=True)
        return {"success": False, "error": str(exc)}
