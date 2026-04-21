import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

from handlers import viewport_commands  # noqa: E402


class FakePaths:
    def __init__(self, saved):
        self._saved = saved

    def project_saved_dir(self):
        return self._saved

    def screen_shot_dir(self):
        return os.path.join(self._saved, "Screenshots")


class FakeAutomation:
    def __init__(self, write_target=None):
        self.calls = []
        self.write_target = write_target

    def take_high_res_screenshot(self, w, h, path, *args, **kwargs):
        self.calls.append((w, h, path, args, kwargs))
        if self.write_target is not None:
            with open(path, "wb") as fh:
                fh.write(self.write_target)


def _make_unreal(tmp_path, automation=None, system=None, write_png=b"\x89PNGfake"):
    import types
    mod = types.SimpleNamespace()
    mod.Paths = FakePaths(str(tmp_path))
    mod.AutomationLibrary = automation if automation is not None else FakeAutomation(write_target=write_png)
    mod.SystemLibrary = system
    return mod


def test_capture_unavailable_without_editor(monkeypatch):
    monkeypatch.setattr(viewport_commands, "_get_unreal_module", lambda: None)
    resp = viewport_commands.handle_capture_editor_viewport({})
    assert resp["error_code"] == "UNAVAILABLE_OUTSIDE_EDITOR"


def test_capture_writes_file_and_returns_base64(monkeypatch, tmp_path):
    png_bytes = b"\x89PNG\r\n\x1a\nHELLO"
    automation = FakeAutomation(write_target=png_bytes)
    mod = _make_unreal(tmp_path, automation=automation)
    monkeypatch.setattr(viewport_commands, "_get_unreal_module", lambda: mod)

    resp = viewport_commands.handle_capture_editor_viewport(
        {"filename": "shot", "width": 640, "height": 360, "timeout": 2}
    )
    assert resp["success"] is True, resp
    assert resp["data"]["format"] == "png"
    assert resp["data"]["path"].endswith("shot.png")
    assert base64.b64decode(resp["data"]["image_base64"]) == png_bytes
    assert automation.calls and automation.calls[0][0] == 640


def test_capture_fails_when_no_capture_api(monkeypatch, tmp_path):
    import types
    mod = types.SimpleNamespace()
    mod.Paths = FakePaths(str(tmp_path))
    mod.AutomationLibrary = None
    mod.SystemLibrary = None
    monkeypatch.setattr(viewport_commands, "_get_unreal_module", lambda: mod)
    resp = viewport_commands.handle_capture_editor_viewport({"timeout": 0.2})
    assert resp["error_code"] == "VIEWPORT_CAPTURE_FAILED"


def test_capture_fails_when_file_never_written(monkeypatch, tmp_path):
    class SilentAutomation:
        def take_high_res_screenshot(self, *args, **kwargs):
            pass  # never writes file

    mod = _make_unreal(tmp_path, automation=SilentAutomation())
    monkeypatch.setattr(viewport_commands, "_get_unreal_module", lambda: mod)
    resp = viewport_commands.handle_capture_editor_viewport({"timeout": 0.3})
    assert resp["error_code"] == "VIEWPORT_CAPTURE_FAILED"
    assert "expected_path" in resp.get("data", {})
