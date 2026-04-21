import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

from utils import level_safety  # noqa: E402


def test_is_forbidden_streaming_class_matches_case_insensitively():
    assert level_safety.is_forbidden_streaming_class("LevelStreamingLevelInstanceEditor")
    assert level_safety.is_forbidden_streaming_class("levelstreaminglevelinstanceeditor")
    assert level_safety.is_forbidden_streaming_class("/Script/Engine.LevelStreamingLevelInstanceEditor")


def test_is_forbidden_streaming_class_ignores_safe_names():
    assert not level_safety.is_forbidden_streaming_class("LevelStreamingDynamic")
    assert not level_safety.is_forbidden_streaming_class("LevelStreamingAlwaysLoaded")
    assert not level_safety.is_forbidden_streaming_class("")
    assert not level_safety.is_forbidden_streaming_class(None)


def test_validate_add_level_mode_normalizes_known_modes():
    assert level_safety.validate_add_level_mode("Sublevel") == "sublevel"
    assert level_safety.validate_add_level_mode("level-instance") == "level_instance"
    assert level_safety.validate_add_level_mode("PACKED_LEVEL") == "packed_level"


def test_validate_add_level_mode_rejects_unknown_modes():
    import pytest
    with pytest.raises(ValueError):
        level_safety.validate_add_level_mode("instanced")


def test_resolve_template_accepts_known_shortcuts_and_passthrough():
    assert level_safety.resolve_template("Basic").endswith("Template_Default")
    assert level_safety.resolve_template("open-world").endswith("OpenWorld")
    assert level_safety.resolve_template("/Engine/Maps/Templates/Foo") == "/Engine/Maps/Templates/Foo"
    assert level_safety.resolve_template("/Game/Maps/MyTemplate") == "/Game/Maps/MyTemplate"


def test_resolve_template_rejects_bogus_names():
    import pytest
    with pytest.raises(ValueError):
        level_safety.resolve_template("Bogus")
    with pytest.raises(ValueError):
        level_safety.resolve_template("")


def test_normalize_actor_names_deduplicates_and_strips():
    names = ["Wall_01", "  Wall_01  ", "", None, "Wall_02"]
    assert level_safety.normalize_actor_names(names) == ["Wall_01", "Wall_02"]


def test_validate_level_asset_path_requires_asset_prefix():
    assert level_safety.validate_level_asset_path("/Game/Maps/Foo") == "/Game/Maps/Foo"
    import pytest
    with pytest.raises(ValueError):
        level_safety.validate_level_asset_path("")
    with pytest.raises(ValueError):
        level_safety.validate_level_asset_path("not-a-path")


def test_build_refusal_hint_mentions_both_recommended_tools():
    hint = level_safety.build_refusal_hint()
    assert "spawn_level_instance" in hint
    assert "create_level_instance_from_selection" in hint
