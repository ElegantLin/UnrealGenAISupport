import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text(encoding="utf-8")


def _extract_function(source: str, signature: str, next_signature: str) -> str:
    pattern = re.compile(rf"{re.escape(signature)}.*?(?={re.escape(next_signature)})", re.S)
    match = pattern.search(source)
    assert match, f"Could not find block for {signature}"
    return match.group(0)


def test_socket_dispatcher_has_no_duplicate_literal_command_keys():
    tree = ast.parse(_read("Content/Python/unreal_socket_server.py"))
    duplicates = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        seen = {}
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                if key.value in seen:
                    duplicates.append(key.value)
                seen[key.value] = True

    assert duplicates == []


def test_mcp_server_has_unique_top_level_tool_function_names():
    tree = ast.parse(_read("Content/Python/mcp_server.py"))
    seen = {}
    duplicates = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in seen:
                duplicates.append(node.name)
            seen[node.name] = True

    assert duplicates == []


def test_legacy_screenshot_tool_does_not_capture_the_desktop():
    source = _read("Content/Python/mcp_server.py")

    assert "import mss" not in source
    assert "mss.mss" not in source
    assert "primary monitor" not in source.casefold()


def test_anim_state_machine_creation_installs_real_animgraph_node():
    source = _read("Source/GenerativeAISupportEditor/Private/MCP/GenAnimationBlueprintUtils.cpp")
    block = _extract_function(
        source,
        "FString UGenAnimationBlueprintUtils::CreateStateMachine",
        "FString UGenAnimationBlueprintUtils::CreateState",
    )

    assert "NewObject<UAnimGraphNode_StateMachine>" in block
    assert "PostPlacedNewNode" in block
    assert "RenameGraph" in block
    assert "FunctionGraphs.Add(SMGraph)" not in block


def test_blend_space_axis_persists_modified_axis_parameter():
    source = _read("Source/GenerativeAISupportEditor/Private/MCP/GenAnimationAssetUtils.cpp")
    block = _extract_function(
        source,
        "FString UGenAnimationAssetUtils::SetBlendSpaceAxis",
        "FString UGenAnimationAssetUtils::ReplaceBlendSpaceSamples",
    )

    assert "ContainerPtrToValuePtr" in block
    assert "BlendParametersProperty" in block
    assert "CopySingleValue" in block
    assert "CopyCompleteValue" not in block
    assert "ResampleData()" in block


def test_verify_asset_aggregates_failed_checks():
    source = _read("Source/GenerativeAISupportEditor/Private/MCP/GenAssetTransactionUtils.cpp")
    block = _extract_function(
        source,
        "FString UGenAssetTransactionUtils::VerifyAsset",
        "bool UGenAssetTransactionUtils::DiscardSnapshot",
    )

    assert "bAllPassed" in block
    assert 'SetBoolField(TEXT("passed"), bAllPassed)' in block


def test_init_unreal_attempts_best_effort_session_restore():
    source = _read("Content/Python/init_unreal.py")

    assert "restore_editor_session" in source
    assert "assets_only" in source


def test_editor_session_focus_uses_kismet_graph_and_node_focus():
    source = _read("Source/GenerativeAISupportEditor/Private/MCP/GenEditorSessionUtils.cpp")

    assert "BringKismetToFocusAttentionOnObject(Graph)" in source
    assert "BringKismetToFocusAttentionOnObject(Node)" in source
    assert "LastEditedDocuments" in source
    assert "LastMcpFocusedGraph" in source
    assert "best-effort" not in source
