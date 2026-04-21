# Unreal MCP Gap Notes and Level Instance Crash Postmortem

Date: 2026-04-20 (status update: 2026-04-21)
Project: `Room_w_skills`
Engine: UE 5.4.4

## Status Summary (2026-04-21)

All eight gaps below are implemented in code behind the standard MCP handler pattern (pure-Python handler + `_get_unreal_module()` + pytest monkeypatch). 226 unit tests pass (`python3.11 -m pytest tests/python/`). The `LevelStreamingLevelInstanceEditor` crash path is now refused with structured error code `LEVEL_INSTANCE_UNSAFE` in both `handlers/level_commands.py` and `utils/safety.py::DESTRUCTIVE_SUBSTRINGS`.

Real-editor validation on UE 5.4.4 is still pending — see "Real-machine validation still required" at the bottom.

## Purpose

This note records two things from the failed UE editor automation session:

1. What the current Unreal MCP surface was missing or not robust enough to handle.
2. Why the editor crashed when trying to complete the "Create Level Instance" part of the workflow.

The goal is to leave a concrete reference for future MCP work, not a generic crash summary.

## Session Outcome

Before the crash, the session successfully created and saved:

- `/Game/Maps/CodexMCP_HousePrefab`
- `/Game/Maps/CodexMCP_HouseShowcase`

The last saved copies were written before the crash. Evidence exists in the crash log:

- `CodexMCP_HousePrefab.umap` save records appear multiple times.
- `CodexMCP_HouseShowcase.umap` save records appear multiple times, including immediately before the failing experiment.

Relevant file:

- `~/Library/Application Support/Epic/UnrealEngine/5.4/Saved/Crashes/CrashReport-UE-Room_w_skills-pid-83509-79D859E07C43BDB550ADDD9D3CCA8F69/Room_w_skills.log`

## MCP Gaps Observed in This Session

### 1. No first-class tool for creating a level from a template

**Status: ✅ Done** — `handlers/level_commands.py::handle_create_level_from_template` + `create_level_from_template` MCP tool. Uses `LevelEditorSubsystem.new_level_from_template` with the shorthand map in `utils/level_safety.KNOWN_LEVEL_TEMPLATES` (`basic`, `default`, `empty`, `openworld`, `open_world`) or any explicit `/Engine`/`/Game` asset path.

The workflow needed the equivalent of:

- `File > New Level`
- choose `Basic`

This had to be done through raw Python (`EditorLevelLibrary.new_level_from_template`) instead of a dedicated MCP tool.

Recommended MCP addition:

- `create_level_from_template(level_path, template_name_or_path)`

Minimum supported templates:

- `Basic`
- `Empty`
- explicit engine template asset path

## 2. No first-class landscape creation workflow

**Status: ✅ Done** — `handlers/landscape_commands.py` provides `create_landscape` and `set_landscape_material`. Returns `LANDSCAPE_UNAVAILABLE` when `unreal.Landscape` is missing from the engine build so callers can fall back cleanly.

The requested workflow explicitly required:

- switching to Landscape mode
- creating a landscape at `0,0,0`
- assigning `M_Ground_Grass` as the landscape material

In practice, there was no reliable MCP tool for:

- creating a landscape actor
- invoking the editor landscape creation flow
- assigning landscape material as part of a safe high-level operation

The Python surface available in this session exposed `Landscape` classes, but spawning them only created `LandscapePlaceholder`, not a usable landscape.

Fallback used during the session:

- a large plane with `M_Ground_Grass`

Recommended MCP additions:

- `create_landscape(transform, size, section_settings, material_path)`
- `set_landscape_material(actor_name_or_path, material_path)`

## 3. No safe "Create Level Instance from selected actors" tool

**Status: ✅ Done** — `handlers/level_commands.py` provides `create_level_instance_from_selection`, `spawn_level_instance`, and `list_level_instances`. Selection-based creation delegates to `LevelInstanceSubsystem.create_level_instance_from`; signature variance across engine builds surfaces as `LEVEL_OPERATION_FAILED` with a remediation hint instead of a raw exception.

This was the most important missing feature for the requested workflow.

The user asked for the editor workflow equivalent of:

- select all house actors
- `Level > Create Level Instance`

There was no MCP tool that directly wrapped that editor action.

Because of that, the session had to fall back to lower-level Python/editor APIs, which is what led to the crash.

Recommended MCP additions:

- `create_level_instance_from_selection(output_level_path, pivot_mode, external_actors)`
- `spawn_level_instance(level_asset_path, transform, runtime_behavior)`
- `list_level_instances()`

## 4. No reliable tool for adding a sublevel or instance to the world with guardrails

**Status: ✅ Done** — `handlers/level_commands.py::handle_add_level_to_world` validates `mode` against `{sublevel, level_instance, packed_level}` and **refuses** `LevelStreamingLevelInstanceEditor` (plus `/Script/Engine.LevelStreamingLevelInstanceEditor`) via `utils/level_safety.is_forbidden_streaming_class`, returning `LEVEL_INSTANCE_UNSAFE`. `execute_python` classification now treats `add_level_to_world_with_transform` + that class as destructive through `utils/safety.DESTRUCTIVE_SUBSTRINGS`.

There is currently no MCP layer that safely handles:

- non-World-Partition main levels
- `LevelInstance` vs `LevelStreamingLevelInstanceEditor`
- runtime behavior selection
- preflight validation before a streaming/instance load

This forced the session to call raw editor scripting APIs directly.

Recommended MCP additions:

- `add_level_to_world(level_path, transform, mode)`

Where `mode` is explicitly constrained, for example:

- `sublevel`
- `level_instance`
- `packed_level`

And validated against engine/project/world constraints before execution.

## 5. `get_all_scene_objects` was broken

**Status: ✅ Done** — `handlers/basic_commands.py::handle_get_all_scene_objects` now uses `EditorActorSubsystem.get_all_level_actors()` with a `GameplayStatics.get_all_actors_of_class` fallback via `UnrealEditorSubsystem.get_editor_world()`. Legacy `EditorLevelLibrary.get_level` is no longer on the path. Response now includes `label` and `count` fields.

The existing tool failed with:

- `Failed: type object 'EditorLevelLibrary' has no attribute 'get_level'`

That means the current implementation is using an outdated or incorrect editor API path.

Impact:

- live scene inspection was not trustworthy through the MCP wrapper
- verification had to be done via raw Python instead

Recommended fix:

- reimplement using `EditorLevelLibrary.get_all_level_actors()` or `EditorActorSubsystem`
- add a regression test against UE 5.4

## 6. No safe viewport screenshot tool

**Status: ✅ Done** — `handlers/viewport_commands.py::handle_capture_editor_viewport` uses `AutomationLibrary.take_high_res_screenshot` (with `HighResShot` console-command fallback) and writes into `Paths.screen_shot_dir()` / `Paths.project_saved_dir()/Screenshots`. No `mss` / desktop-level capture. Returns the PNG contents as base64 alongside the on-disk path.

The available screenshot tool captured the entire primary monitor through an OS-level path.

That was blocked for privacy reasons, which is correct, but it left no safe way to verify the editor view.

Recommended MCP addition:

- `capture_editor_viewport()`

Requirements:

- capture only Unreal editor viewport content
- do not capture the full desktop
- return an image usable for visual verification

## 7. No first-class project settings editing for common rendering toggles

**Status: ✅ Done** — `handlers/project_settings_commands.py` provides generic `set_project_setting(settings_class, key, value)` and a curated `set_rendering_defaults` that maps `auto_exposure`, `motion_blur`, `bloom`, `ambient_occlusion`, `lens_flares`, `anti_aliasing` to their `RendererSettings` UPROPERTYs. Uses the settings CDO and calls `save_config()` to persist.

The requested workflow included disabling auto exposure.

This session had to inspect config files and Python surfaces manually. There was no direct tool for common project settings changes.

Recommended MCP additions:

- `set_project_setting(section, key, value)`
- or a smaller curated tool such as `set_rendering_defaults(auto_exposure=false, ...)`

## 8. No high-level batch actor operations matching normal editor workflows

**Status: ✅ Done** — `handlers/actor_batch_commands.py` provides `duplicate_actors`, `replace_static_mesh`, `replace_material`, `group_actors`, and `select_actors` (with `exact` / `contains` / `prefix` match modes). Kept in a dedicated module to avoid the unconditional `import unreal` in `actor_commands.py` and stay importable under pytest.

The requested workflow also relied on common editor operations:

- duplicate with offset
- replace selected wall meshes
- batch apply materials
- group actors
- operate on outliner-filtered selections

Most of these had to be approximated with custom Python instead of one-step MCP calls.

Recommended MCP additions:

- `duplicate_actors(actor_names, offset)`
- `replace_static_mesh(actor_names, mesh_path)`
- `replace_material(actor_names, material_path, slot_index)`
- `group_actors(actor_names, group_name)`
- `select_actors(query)`

## Crash Summary

### What command caused the crash

The final Python script executed before the crash was:

- `Saved/Temp/PythonExec/script_114c0c53b4cc4894b850cb396e7ebd0f.py`

Most relevant line:

- line 7:
  `stream = level_utils.add_level_to_world_with_transform(world, '/Game/Maps/CodexMCP_HousePrefab', unreal.LevelStreamingLevelInstanceEditor, transform)`

Reference:

- `/Users/zonglindi/Documents/Unreal Projects/Room_w_skills/Saved/Temp/PythonExec/script_114c0c53b4cc4894b850cb396e7ebd0f.py`

## Why this path was attempted

The session tried safer options first:

1. create the reusable house as its own map
2. spawn a `LevelInstance` actor in the showcase map
3. bind it to `/Game/Maps/CodexMCP_HousePrefab`

That actor path did not load correctly in the `Basic`-style showcase map, so the next attempt was to use editor level-streaming instance APIs to add the prefab map with a transform.

The goal was still the same user requirement: make the house reusable as a level instance.

## Crash Evidence

### Crash report summary

Crash report:

- `~/Library/Application Support/Epic/UnrealEngine/5.4/Saved/Crashes/CrashReport-UE-Room_w_skills-pid-83509-79D859E07C43BDB550ADDD9D3CCA8F69/CrashContext.runtime-xml`

Relevant lines:

- `ErrorMessage`: `Cast of nullptr to Actor failed`
- call stack includes:
  - `ULevelInstanceSubsystem::FLevelInstanceEdit::FLevelInstanceEdit`
  - `ULevelInstanceSubsystem::RegisterLoadedLevelStreamingLevelInstanceEditor`
  - `ULevelStreamingLevelInstanceEditor::OnLevelLoadedChanged`
  - `UEditorLevelUtils::AddLevelToWorld`
  - `UEditorLevelUtils::execK2_AddLevelToWorldWithTransform`

### Editor log summary

Crash log:

- `~/Library/Application Support/Epic/UnrealEngine/5.4/Saved/Crashes/CrashReport-UE-Room_w_skills-pid-83509-79D859E07C43BDB550ADDD9D3CCA8F69/Room_w_skills.log`

Relevant sequence:

1. MCP plugin receives the Python command.
2. `CodexMCP_HouseShowcase.umap` is loaded.
3. `ULevelStreaming::RequestLevel(/Game/Maps/CodexMCP_HousePrefab)` begins.
4. Script stack shows `EditorLevelUtils.K2_AddLevelToWorldWithTransform`.
5. Fatal error:
   `Cast of nullptr to Actor failed`
6. Followed by `SIGSEGV`.

## Root Cause

### Evidence-based statement

The crash happened inside Unreal Editor itself while processing:

- `AddLevelToWorldWithTransform`
- with streaming class `LevelStreamingLevelInstanceEditor`

The crash report shows Unreal attempting to register a loaded editor level instance and then fatally failing on:

- `CastChecked<AActor, ILevelInstanceInterface>(ILevelInstanceInterface*)`

with a null pointer.

### Likely engine-side interpretation

This strongly suggests the code path expected a valid backing actor implementing `ILevelInstanceInterface`, but in this usage path that object was null.

In other words:

- the operation created or loaded a streaming level
- Unreal then treated it as an editor level instance
- the registration code assumed an actor/interface object existed
- that assumption was false
- the engine asserted and then crashed

This looks like an engine/editor API assumption mismatch, not a content error in the house map itself.

## What did not cause the crash

The following were not the primary cause:

- floor/wall/roof mesh placement
- applying `M_Ground_Grass`, `M_Brick_Clay_New`, or `M_Rock_Marble_Polished`
- point light creation
- saving `CodexMCP_HousePrefab`
- saving `CodexMCP_HouseShowcase`

Those operations completed successfully before the failing experiment.

## Immediate MCP Recommendations

### High priority

1. [x] Add a dedicated `create_level_instance_from_selection` tool. *(Done: `handlers/level_commands.py`)*
2. [x] Add a dedicated `spawn_level_instance` or `add_sublevel_instance` tool. *(Done: `spawn_level_instance` + `add_level_to_world`)*
3. [x] Fix `get_all_scene_objects`. *(Done: `handlers/basic_commands.py`)*
4. [x] Add a safe editor viewport capture tool. *(Done: `handlers/viewport_commands.py`)*

### Medium priority

5. [x] Add `create_level_from_template`. *(Done: `handlers/level_commands.py`)*
6. [x] Add `create_landscape`. *(Done: `handlers/landscape_commands.py`)*
7. [x] Add first-class project setting edits for common rendering options. *(Done: `handlers/project_settings_commands.py`)*
8. [x] Add selection, grouping, duplication, and batch replace utilities. *(Done: `handlers/actor_batch_commands.py`)*

## Guardrails MCP Should Enforce

**Status: ✅ Implemented in code.** Before any tool uses a level-instance or editor-streaming path, MCP now validates:

1. [x] engine version compatibility *(capabilities surface via `get_capabilities`)*
2. [ ] whether the main map is World Partition or not *(not yet surfaced — requires editor-side inspection)*
3. [x] whether the requested class is intended for editor-only use *(`utils/level_safety.is_forbidden_streaming_class`)*
4. [x] whether a backing actor implementing the expected interface exists *(handled by refusing the crashing path entirely rather than attempting the cast)*
5. [x] whether the operation is known-safe for the chosen map type *(`validate_add_level_mode` against the `{sublevel, level_instance, packed_level}` allowlist)*

If any of those checks fail, MCP returns a structured `LEVEL_INSTANCE_UNSAFE` / `LEVEL_MODE_UNSUPPORTED` error instead of executing raw Python.

## Dangerous API Combination to Avoid

**Status: ✅ Refused by MCP.** The combination below is blocked in two layers:

- `handlers/level_commands.py::handle_add_level_to_world` refuses the class with `LEVEL_INSTANCE_UNSAFE` before invoking `EditorLevelUtils.add_level_to_world`.
- `utils/safety.DESTRUCTIVE_SUBSTRINGS` now flags any raw `execute_python` script that combines `add_level_to_world_with_transform` with `levelstreaminglevelinstanceeditor`.

Until explicitly validated on UE 5.4.4, do not use this combination from MCP:

`EditorLevelUtils.add_level_to_world_with_transform(..., unreal.LevelStreamingLevelInstanceEditor, ...)`

That exact path is the one that triggered the crash in this session.

## Real-machine validation still required

Everything above passes the pytest monkeypatch suite but has **not** yet been exercised against a live UE 5.4.4 editor. Before declaring each tool production-ready, verify on a real project:

1. `create_level_instance_from_selection` — actual `LevelInstanceSubsystem.create_level_instance_from` signature on the target engine build (the handler tolerates TypeError with a remediation hint, but a real run confirms the happy path).
2. `spawn_level_instance` — whether `set_world_asset` or `set_editor_property("world_asset", ...)` is the one accepted by this build's `ALevelInstance`.
3. `add_level_to_world(mode="sublevel")` — ensure `EditorLevelUtils.add_level_to_world` actually mounts the sublevel rather than silently failing.
4. `add_level_to_world(streaming_class="LevelStreamingLevelInstanceEditor")` — confirm the refusal path returns `LEVEL_INSTANCE_UNSAFE` without touching the engine.
5. `create_landscape` — verify `landscape_material` assignment sticks after the actor is registered (UE is picky about assignment order for landscapes).
6. `capture_editor_viewport` — confirm the PNG lands in `Saved/Screenshots` and that the base64 payload round-trips.
7. `set_project_setting` / `set_rendering_defaults` — confirm `save_config()` writes to the expected `.ini` path and persists after editor restart.
8. `get_all_scene_objects` — sanity-check on a populated level and confirm the previous `EditorLevelLibrary.get_level` error no longer appears.
9. Batch actor ops (`duplicate_actors`, `replace_static_mesh`, `replace_material`, `group_actors`, `select_actors`) — spot-check on a small sublevel.

## Safer Short-Term Workarounds

Until MCP has proper level-instance support, prefer one of these:

1. keep the reusable house as a normal saved map/sublevel and load it through a validated sublevel tool
2. build the house directly in the target map
3. wrap the house in a Blueprint or packed representation through a dedicated safe pipeline
4. avoid editor-only level-instance streaming classes from raw Python

## Files Worth Keeping for MCP Work

Primary evidence:

- `Saved/Temp/PythonExec/script_114c0c53b4cc4894b850cb396e7ebd0f.py`
- `~/Library/Application Support/Epic/UnrealEngine/5.4/Saved/Crashes/CrashReport-UE-Room_w_skills-pid-83509-79D859E07C43BDB550ADDD9D3CCA8F69/CrashContext.runtime-xml`
- `~/Library/Application Support/Epic/UnrealEngine/5.4/Saved/Crashes/CrashReport-UE-Room_w_skills-pid-83509-79D859E07C43BDB550ADDD9D3CCA8F69/Room_w_skills.log`

Generated maps from the session:

- `/Game/Maps/CodexMCP_HousePrefab`
- `/Game/Maps/CodexMCP_HouseShowcase`
