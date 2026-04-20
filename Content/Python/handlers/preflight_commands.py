from typing import Any, Dict, Iterable, List, Sequence

try:
    from utils.mcp_response import err, ok
except ImportError:
    from Content.Python.utils.mcp_response import err, ok


DEFAULT_EDITOR_SCRIPTING_DEPENDENCIES = (
    "PythonScriptPlugin",
    "EditorScriptingUtilities",
)

_ARCHITECTURE_ALIASES = {
    "amd64": "x64",
    "arm64": "arm64",
    "aarch64": "arm64",
    "win64": "x64",
    "x64": "x64",
    "x8664": "x64",
}


def _normalize_architecture_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"

    normalized_key = text.casefold().replace("-", "").replace("_", "")
    return _ARCHITECTURE_ALIASES.get(normalized_key, text)


def _unique_strings(values: Iterable[Any]) -> List[str]:
    unique_values: List[str] = []
    seen = set()

    for value in values or []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique_values.append(text)

    return unique_values


def _normalize_architecture_values(values: Iterable[Any]) -> List[str]:
    normalized = []
    seen = set()

    for value in values or []:
        architecture = _normalize_architecture_name(value)
        if architecture == "unknown" or architecture in seen:
            continue
        seen.add(architecture)
        normalized.append(architecture)

    return normalized


def _build_check(name: str, is_ok: bool, message: str, severity: str = "error", **extra: Any) -> Dict[str, Any]:
    payload = {
        "name": name,
        "ok": bool(is_ok),
        "severity": severity,
        "message": message,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _find_missing_plugins(required: Sequence[str], enabled: Sequence[str]) -> List[str]:
    enabled_lookup = {plugin.casefold(): plugin for plugin in _unique_strings(enabled)}
    missing = []

    for plugin_name in _unique_strings(required):
        if plugin_name.casefold() not in enabled_lookup:
            missing.append(plugin_name)

    return missing


def build_arch_mismatch_warning(editor_arch: Any, module_architectures: Iterable[Any]) -> str:
    normalized_editor_arch = _normalize_architecture_name(editor_arch)
    normalized_module_architectures = _normalize_architecture_values(module_architectures)
    module_text = ", ".join(normalized_module_architectures) or "unknown"
    return (
        f"Architecture mismatch: editor is {normalized_editor_arch} but modules are {module_text}. "
        "Rebuild the affected binaries for the active editor architecture."
    )


def summarize_preflight_checks(checks: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    normalized_checks = [dict(check) for check in checks]
    failed_checks = [
        check
        for check in normalized_checks
        if not check.get("ok") and check.get("severity", "error") != "warning"
    ]
    warning_checks = [
        check
        for check in normalized_checks
        if not check.get("ok") and check.get("severity", "error") == "warning"
    ]
    return {
        "ok": not failed_checks,
        "checks": normalized_checks,
        "failed_checks": failed_checks,
        "warning_checks": warning_checks,
        "blocking_issue_count": len(failed_checks),
        "warning_count": len(warning_checks),
    }


def _build_preflight_checks(
    context: Dict[str, Any],
    *,
    required_plugins: Sequence[str],
    required_editor_dependencies: Sequence[str],
) -> List[Dict[str, Any]]:
    project_file_path = str(context.get("project_file_path", "") or "").strip()
    editor_path = str(context.get("editor_path", "") or "").strip()
    editor_arch = _normalize_architecture_name(context.get("editor_binary_architecture"))
    project_arch = _normalize_architecture_name(context.get("project_target_architecture"))
    module_architectures = _normalize_architecture_values(context.get("module_architectures", []))
    enabled_plugins = _unique_strings(context.get("enabled_plugins", []))
    dirty_assets = _unique_strings(context.get("dirty_assets", []))
    open_asset_paths = _unique_strings(context.get("open_asset_paths", []))
    input_system = str(context.get("input_system", "") or "").strip() or "unknown"

    checks = [
        _build_check(
            "uproject",
            bool(project_file_path),
            "Resolved the active .uproject path." if project_file_path else "Could not resolve the current .uproject path.",
            project_file_path=project_file_path,
        ),
        _build_check(
            "editor_path",
            bool(editor_path),
            "Resolved the Unreal Editor executable path." if editor_path else "Could not resolve the Unreal Editor executable path.",
            editor_path=editor_path,
        ),
    ]

    if project_arch == "unknown":
        checks.append(
            _build_check(
                "project_target_architecture",
                False,
                "Could not determine the project target architecture from the current context.",
                severity="warning",
                project_target_architecture=project_arch,
            )
        )
    else:
        checks.append(
            _build_check(
                "project_target_architecture",
                True,
                f"Project target architecture appears to be {project_arch}.",
                project_target_architecture=project_arch,
            )
        )

    if module_architectures:
        checks.append(
            _build_check(
                "module_architectures",
                True,
                f"Detected module architectures: {', '.join(module_architectures)}.",
                module_architectures=module_architectures,
            )
        )
    else:
        checks.append(
            _build_check(
                "module_architectures",
                False,
                "Could not determine binary module architectures from the available project and plugin binaries.",
                severity="warning",
                module_architectures=[],
            )
        )

    if editor_arch != "unknown" and module_architectures:
        mismatched_architectures = [arch for arch in module_architectures if arch != editor_arch]
        if mismatched_architectures:
            checks.append(
                _build_check(
                    "architecture_compatibility",
                    False,
                    build_arch_mismatch_warning(editor_arch, module_architectures),
                    editor_binary_architecture=editor_arch,
                    module_architectures=module_architectures,
                )
            )
        else:
            checks.append(
                _build_check(
                    "architecture_compatibility",
                    True,
                    f"Editor and module architectures agree on {editor_arch}.",
                    editor_binary_architecture=editor_arch,
                    module_architectures=module_architectures,
                )
            )
    else:
        checks.append(
            _build_check(
                "architecture_compatibility",
                False,
                "Could not fully verify architecture compatibility from the available context.",
                severity="warning",
                editor_binary_architecture=editor_arch,
                module_architectures=module_architectures,
            )
        )

    missing_required_plugins = _find_missing_plugins(required_plugins, enabled_plugins)
    checks.append(
        _build_check(
            "plugin_state",
            not missing_required_plugins,
            (
                f"Required project plugins are enabled: {', '.join(_unique_strings(required_plugins))}."
                if required_plugins and not missing_required_plugins
                else (
                    f"Missing required project plugins: {', '.join(missing_required_plugins)}."
                    if missing_required_plugins
                    else "No additional project plugin requirements were requested."
                )
            ),
            missing_plugins=missing_required_plugins,
            enabled_plugins=enabled_plugins,
        )
    )

    missing_editor_dependencies = _find_missing_plugins(required_editor_dependencies, enabled_plugins)
    checks.append(
        _build_check(
            "editor_scripting_dependencies",
            not missing_editor_dependencies,
            (
                "Editor scripting dependencies appear to be enabled."
                if not missing_editor_dependencies
                else (
                    "Could not verify all editor scripting dependencies from the current project descriptor. "
                    f"Missing or not listed: {', '.join(missing_editor_dependencies)}."
                )
            ),
            severity="warning" if missing_editor_dependencies else "error",
            missing_plugins=missing_editor_dependencies,
            enabled_plugins=enabled_plugins,
        )
    )

    checks.append(
        _build_check(
            "input_system",
            input_system.casefold() not in ("", "unknown"),
            (
                f"Input system detected: {input_system}."
                if input_system.casefold() not in ("", "unknown")
                else "Could not determine the active input system."
            ),
            severity="warning" if input_system.casefold() in ("", "unknown") else "error",
            input_system=input_system,
        )
    )

    checks.append(
        _build_check(
            "editor_state",
            True,
            f"Detected {len(dirty_assets)} dirty assets and {len(open_asset_paths)} open asset editors.",
            dirty_assets=dirty_assets,
            open_asset_paths=open_asset_paths,
        )
    )

    return checks


def _load_plugin_commands_module():
    try:
        from handlers import plugin_commands
    except ImportError:
        from Content.Python.handlers import plugin_commands
    return plugin_commands


def handle_preflight_project(command: Dict[str, Any]) -> Dict[str, Any]:
    try:
        context = command.get("editor_context")
        if not isinstance(context, dict):
            context = _load_plugin_commands_module().handle_get_editor_context(command)

        if not isinstance(context, dict):
            return err(
                "Preflight did not receive a valid editor context payload.",
                error_code="PREFLIGHT_CONTEXT_INVALID",
            )

        required_plugins = _unique_strings(command.get("required_plugins", []))
        required_editor_dependencies = _unique_strings(
            command.get("required_editor_scripting_dependencies", DEFAULT_EDITOR_SCRIPTING_DEPENDENCIES)
        )

        checks = _build_preflight_checks(
            context,
            required_plugins=required_plugins,
            required_editor_dependencies=required_editor_dependencies,
        )
        summary = summarize_preflight_checks(checks)
        warnings = [
            check["message"]
            for check in summary["warning_checks"]
            if check.get("message")
        ]
        message = "Preflight complete." if summary["ok"] else "Preflight found blocking issues."

        return ok(
            message,
            data={
                **summary,
                "context": context,
            },
            warnings=warnings,
        )
    except Exception as exc:
        return err(
            f"Failed to run project preflight: {exc}",
            error_code="PREFLIGHT_FAILED",
        )
