from Content.Python.handlers import preflight_commands


def test_build_arch_mismatch_warning_mentions_editor_and_module_arch():
    message = preflight_commands.build_arch_mismatch_warning("arm64", ["x64"])
    assert "arm64" in message
    assert "x64" in message
    assert "rebuild" in message.lower()


def test_summarize_preflight_checks_separates_failures_from_warnings():
    summary = preflight_commands.summarize_preflight_checks([
        {"name": "uproject", "ok": True, "severity": "error"},
        {"name": "editor_path", "ok": False, "severity": "error", "message": "Missing editor path"},
        {"name": "input_system", "ok": False, "severity": "warning", "message": "Unknown input system"},
    ])

    assert summary["ok"] is False
    assert [check["name"] for check in summary["failed_checks"]] == ["editor_path"]
    assert [check["name"] for check in summary["warning_checks"]] == ["input_system"]


def test_handle_preflight_project_wraps_summary_and_warnings(monkeypatch):
    class FakePluginCommands:
        @staticmethod
        def handle_get_editor_context(_command):
            return {
                "success": True,
                "project_file_path": "/Projects/Test/Test.uproject",
                "project_dir": "/Projects/Test",
                "engine_dir": "/Unreal/Engine",
                "editor_path": "/Unreal/Engine/Binaries/Mac/UnrealEditor",
                "editor_path_candidates": [],
                "editor_pid": 1234,
                "platform": "Darwin",
                "dirty_packages": ["/Game/Maps/TestMap"],
                "dirty_package_count": 1,
                "editor_binary_architecture": "arm64",
                "project_target_architecture": "unknown",
                "module_architectures": ["x64"],
                "input_system": "EnhancedInput",
                "enabled_plugins": ["EnhancedInput"],
                "dirty_assets": ["/Game/Maps/TestMap"],
                "open_asset_paths": ["/Game/Blueprints/BP_Test"],
            }

    monkeypatch.setattr(
        preflight_commands,
        "_load_plugin_commands_module",
        lambda: FakePluginCommands,
    )

    payload = preflight_commands.handle_preflight_project({"type": "preflight_project"})

    assert payload["success"] is True
    assert payload["message"] == "Preflight found blocking issues."
    assert payload["data"]["ok"] is False
    assert payload["data"]["context"]["editor_binary_architecture"] == "arm64"
    assert payload["data"]["context"]["module_architectures"] == ["x64"]
    assert payload["data"]["context"]["dirty_assets"] == ["/Game/Maps/TestMap"]
    assert payload["data"]["context"]["open_asset_paths"] == ["/Game/Blueprints/BP_Test"]
    assert any(check["name"] == "architecture_compatibility" for check in payload["data"]["failed_checks"])
    assert any(check["name"] == "project_target_architecture" for check in payload["data"]["warning_checks"])
    assert payload["warnings"]
