from Content.Python.utils.safety import (
    UNSAFE_COMMANDS,
    classify_script,
    is_unsafe_command,
    summarize_dirty_packages,
)


def test_read_only_script_is_classified_read_only():
    result = classify_script("x = unreal.EditorAssetLibrary.does_asset_exist('/Game/A')")
    assert result.classification == "read_only"
    assert result.is_destructive is False
    assert result.requires_force is False


def test_destructive_script_requires_force():
    result = classify_script("unreal.EditorAssetLibrary.delete_asset('/Game/A')")
    assert result.classification == "destructive"
    assert result.is_destructive is True
    assert result.requires_force is True
    assert any("delete_asset" in t for t in result.triggers)


def test_side_effect_script_does_not_require_force_but_is_flagged():
    result = classify_script("blueprint.compile_blueprint()")
    assert result.classification == "side_effect"
    assert result.is_destructive is False
    assert result.requires_force is False
    assert result.triggers


def test_extra_destructive_substrings_are_respected():
    result = classify_script("super_secret_wipe()", extra_destructive=["super_secret_wipe"])
    assert result.classification == "destructive"


def test_summarize_dirty_packages_dedupes_and_keeps_order():
    class PkgLike:
        def __init__(self, name):
            self._name = name

        def get_name(self):
            return self._name

    pkgs = [PkgLike("/Game/A"), "/Game/A", PkgLike("/Game/B"), "  ", None]
    assert summarize_dirty_packages(pkgs) == ["/Game/A", "/Game/B"]


def test_unsafe_command_lookup_is_case_insensitive():
    assert is_unsafe_command("execute_python")
    assert is_unsafe_command("EXECUTE_PYTHON")
    assert not is_unsafe_command("get_capabilities")
    assert "execute_python" in UNSAFE_COMMANDS


def test_classify_handles_empty_input():
    result = classify_script("")
    assert result.classification == "read_only"
    assert result.triggers == []
