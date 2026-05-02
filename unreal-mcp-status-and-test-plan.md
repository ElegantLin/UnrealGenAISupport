# Unreal MCP Status & Real-Machine Test Plan

- Version: `v1.0`
- Date: `2026-05-01`
- Scope: Consolidated status of `GenerativeAISupport` Unreal MCP plugin, merging the reliability improvement roadmap and the level-instance crash post-mortem into a single source of truth.
- Merged source of truth. Supersedes and replaces the historical `unreal-mcp-improvement-plan.md` and `unreal-mcp-level-instance-postmortem.md` files.
- Engine target: UE 5.4.4 (crash evidence collected on this build; core workflows and postmortem smoke paths now partially verified against a live editor on Mac arm64).

---

## 1. TL;DR

| Area | Status | Tests |
|---|---|---|
| Protocol envelope, capabilities, unsafe marking (P0A) | ✅ Done | `test_protocol_contract.py`, `test_mcp_response.py` |
| Preflight + job model + editor context (P0B) | ✅ Done | `test_preflight_commands.py`, `test_job_state.py` |
| Safe mutation runtime: preview/apply/rollback/undo (P1) | ✅ Done | `test_transaction_commands.py`, `test_transactions.py` |
| Blueprint core reliability (P1.25) | ✅ Done | `test_blueprint_inspect_commands.py`, `test_blueprint_graph.py` |
| Session capture/restore + focus navigation (P1.5) | ✅ Mostly done (see §3) | `test_session_commands.py`, `test_session_state.py` |
| Enhanced Input alignment (P2) | ✅ Done | `test_input_commands.py`, `test_input_mapping.py` |
| BlendSpace read/write (P3) | ✅ Done | `test_blend_space.py`, `test_animation_commands.py` |
| AnimBlueprint read (P4) + write (P5) | ✅ Done | `test_anim_blueprint.py`, `test_anim_blueprint_commands.py` |
| Cross-cutting observability (P6) | 🟡 Partial (see §3) | `test_observability.py` |
| Testing & validation (P7) | 🟡 Partial (see §3) | 254 unit tests pass; manual UE 5.4.4 / Mac arm64 smoke completed for core workflows |
| **Level instance / landscape / viewport / settings / batch actor gaps (post-mortem)** | 🟡 Mostly validated (see §1.1) | `test_level_safety.py`, `test_level_commands.py`, `test_landscape_commands.py`, `test_viewport_commands.py`, `test_project_settings_commands.py`, `test_actor_batch_commands.py`; live editor smoke completed where UE Python exposes the needed API |

Run the suite with:

```bash
python3.11 -m pytest tests/python/ -q
```

Current baseline: **254 passed** on 2026-05-01.

Core Blueprint, animation, viewport, input, settings, and postmortem smoke paths have been verified against a running UE 5.4.4 editor. The real-machine test plan in §4 remains the broader production-readiness checklist.

### 1.1 Real-editor validation summary (UE 5.4.4 / Mac arm64)

Validated against `/Users/zonglindi/Documents/ExamplesForUEGenAIPlugin` with the project plugin loaded from `/Users/zonglindi/Documents/ExamplesForUEGenAIPlugin/Plugins/GenerativeAISupport`.

Passed live editor checks:

- Socket handshake, `get_capabilities`, `get_editor_context`, and clean dirty-package reporting.
- Ordinary Blueprint graph schema, full UObject graph path resolution, nodes, pins, and compile diagnostics.
- `open_asset`, `focus_graph`, `capture_editor_session`, and active viewport PNG capture.
- Enhanced Input create/map/list flow.
- BlendSpace read/write after fixing the UE 5.4 `CopySingleValue` crash in axis updates.
- AnimBlueprint structure read plus state machine, state, transition, alias, sequence binding, BlendSpace binding, graph nodes, and graph pins.
- Postmortem paths: `create_level_from_template`, `get_all_scene_objects`, `select_actors`, `duplicate_actors`, `replace_static_mesh`, `replace_material`, `group_actors`, `add_level_to_world(mode="sublevel")`, `spawn_level_instance`, `list_level_instances`, `capture_editor_viewport`, `set_rendering_defaults`, and `LevelStreamingLevelInstanceEditor` refusal.

Live editor results that are intentionally not green:

- `create_level_instance_from_selection` cannot hit a happy path on this UE Python build because `unreal.LevelInstanceSubsystem` / `create_level_instance_from` is not exposed. The handler returns structured `LEVEL_OPERATION_FAILED`.
- `create_landscape` cannot create a usable landscape through this Python surface; UE spawns `LandscapePlaceholder`. MCP now returns `LANDSCAPE_UNAVAILABLE` and removes the placeholder instead of reporting false success.

Real-editor fixes added during validation:

- `actor_batch_commands.handle_duplicate_actors` now calls the UE 5.4 signature as `duplicate_actors(actors, None, offset)`.
- `landscape_commands.handle_create_landscape` detects `LandscapePlaceholder` and returns `LANDSCAPE_UNAVAILABLE`.
- `project_settings_commands._resolve_settings_cdo` prefers `unreal.get_default_object(cls)` for settings CDOs.
- `utils.anim_blueprint.parse_graph_path` accepts full UObject graph paths such as `/Game/ABP.ABP:AnimGraph`.

Verification evidence from the latest pass:

```bash
python3 -m pytest tests/python/ -q
python3 -m compileall -q Content/Python tests/python
git diff --check
RunUAT BuildPlugin -Plugin=/Users/zonglindi/Documents/UnrealGenAISupport/GenerativeAISupport.uplugin -Package=/private/tmp/UnrealGenAISupport_Build -TargetPlatforms=Mac -Rocket
```

Observed results: `254 passed`, compileall passed, diff check passed, BuildPlugin succeeded, and final editor context reported `dirty_package_count == 0`.

---

## 2. What is done (code + unit tests)

### 2.1 Post-mortem gaps (from `Room_w_skills` UE 5.4.4 crash session)

| # | Gap | Status | Implementation |
|---|---|---|---|
| 1 | Create level from template | ✅ | `handlers/level_commands.py::handle_create_level_from_template` (shortcuts via `utils/level_safety.KNOWN_LEVEL_TEMPLATES`) |
| 2 | Landscape creation + material | 🟡 | `handlers/landscape_commands.py::handle_create_landscape`, `handle_set_landscape_material`; real UE 5.4 editor returns `LandscapePlaceholder`, so MCP now returns `LANDSCAPE_UNAVAILABLE` rather than false success |
| 3 | Create Level Instance from selection | 🟡 | `handlers/level_commands.py::handle_create_level_instance_from_selection` + `handle_spawn_level_instance` + `handle_list_level_instances`; `spawn_level_instance` passed live, but selection-based creation is blocked where `unreal.LevelInstanceSubsystem` is not exposed |
| 4 | Safe `add_level_to_world` with guardrails | ✅ | `handlers/level_commands.py::handle_add_level_to_world` refuses `LevelStreamingLevelInstanceEditor` via `utils/level_safety.is_forbidden_streaming_class` → `LEVEL_INSTANCE_UNSAFE`; `mode` restricted to `{sublevel, level_instance, packed_level}` |
| 5 | `get_all_scene_objects` broken on UE 5.4 | ✅ | `handlers/basic_commands.py::handle_get_all_scene_objects` now uses `EditorActorSubsystem.get_all_level_actors` with `GameplayStatics` fallback. Response now includes `label` and `count`. |
| 6 | Safe viewport capture (no full-desktop screenshot) | ✅ | `handlers/viewport_commands.py::handle_capture_editor_viewport` uses `AutomationLibrary.take_high_res_screenshot` with `HighResShot` fallback; returns base64 PNG; writes into `Paths.screen_shot_dir()` / `<Saved>/Screenshots` |
| 7 | Project-settings editing | ✅ | `handlers/project_settings_commands.py::handle_set_project_setting` (generic, settings CDO + `save_config`) + `handle_set_rendering_defaults` with curated `RENDERING_SETTING_MAP` for `auto_exposure`, `motion_blur`, `bloom`, `ambient_occlusion`, `lens_flares`, `anti_aliasing` |
| 8 | Batch actor ops | ✅ | `handlers/actor_batch_commands.py`: `duplicate_actors`, `replace_static_mesh`, `replace_material`, `group_actors`, `select_actors` (match modes: `exact` / `contains` / `prefix`). Kept in a dedicated module to avoid the unconditional `import unreal` in `actor_commands.py` (pytest-friendly) |

Crash-safety guardrails:

- `utils/level_safety.py` is the single source of truth for the refusal. Matches `levelstreaminglevelinstanceeditor` case-insensitively and tolerates `/Script/Engine.` path prefixes.
- `utils/safety.DESTRUCTIVE_SUBSTRINGS` additionally blocks raw `execute_python` that combines `add_level_to_world_with_transform` with `levelstreaminglevelinstanceeditor`.

### 2.2 Improvement-plan phases

- **P0A Protocol Contract** — shared response envelope (`success`, `message`, `data`, `error`, `error_code`, `warnings`, `job_id`, `changed_assets`, `api_version`), normalized tool return shapes, `get_capabilities`, explicit unsafe-path marking.
- **P0B Execution Model + Preflight** — `preflight_project`, expanded `get_editor_context`, job model with `job_id`/progress/cancel, `get_job_status`/`cancel_job`/`list_active_jobs`, per-job Unreal log snapshots (`utils/observability.py`).
- **P1 Safe Mutation Runtime** — `UGenAssetTransactionUtils` (C++ side), `preview_operation`/`apply_operation`, rollback via duplicate-asset strategy, `undo_last_mcp_operation`, `execute_python` downgraded with `read_only`/`dry_run`/`changed_assets`/`recent_logs`/`dirty_packages`.
- **P1.25 Blueprint Core Reliability** — full graph enumeration via `UBlueprint::GetAllGraphs()`, `get_graph_schema`/`get_graph_nodes`/`get_graph_pins`/`resolve_graph_by_path`, `resolve_node_by_selector`/`get_pin_compatibility`/`suggest_autocast_path`, detailed connection diagnostics, real compile diagnostics with warnings/errors.
- **P1.5 Session Restore** — `capture_editor_session`, open-asset capture via `UAssetEditorSubsystem->GetAllEditedAssets()`, auto-snapshot before restart, first-stage restore (Blueprint / AnimBlueprint / AnimationSequence / BlendSpace / WidgetBlueprint / Material), focus restore, `open_asset`/`bring_asset_to_front`/`focus_graph`/`focus_node`/`select_actor`, fault-tolerant `restored_assets` / `failed_assets`.
- **P2 Enhanced Input** — `create_input_action`, `create_input_mapping_context`, `map_enhanced_input_action`, `list_input_mappings`, legacy `add_input_binding` warning in Enhanced-Input projects.
- **P3 BlendSpace** — `get_blend_space_info`, `set_blend_space_axis`, `replace_blend_space_samples`, `set_blend_space_sample_animation`; post-save reload verification.
- **P4 AnimBlueprint Read** — `get_anim_blueprint_structure`, graph inspection APIs, stable selectors for state machines / states / transitions / aliases.
- **P5 AnimBlueprint Write** — `create_state_machine`, `create_state`, `create_transition`, `set_transition_rule`, `create_state_alias`, `set_alias_targets`, `set_state_sequence_asset`, `set_state_blend_space_asset`, `set_cached_pose_node`, `set_default_slot_chain`, `set_apply_additive_chain`. Raw node editing marked unsafe.
- **P6 Observability** — structured mutation reports (`changed_assets`, `compiled_assets`, `saved_assets`, `warnings`, `rollback_performed`, `verification_checks`), Unreal log deltas auto-attached on save/compile failure, unified error codes in `utils/error_codes.py::ALL_ERROR_CODES`.

---

## 3. What is NOT done

### 3.1 Still open in the roadmap

| ID | Item | Phase | Notes |
|---|---|---|---|
| R1 | Restore policy setting (`none` / `assets_only` / `assets_and_tabs`) | P1.5 | Current restore path is hard-coded to assets-only. Needs a plugin setting + handler parameter. |
| R2 | Second-stage tab/layout restoration (Content Browser, World Outliner, Details, Output Log) | P1.5 | Deferred until asset restore is stable on real engine. |
| R3 | Asset diff summaries for write operations | P6 | Mutation reports exist, but no semantic diff of changed assets yet. |
| R4 | Diagnostic bundles persisted to `Saved/MCP` (request payload + normalized args + mutation report + log delta + verify results) | P6 | Partial: log deltas already attach on failure; a consolidated bundle file is not yet written. |
| R5 | Minimal regression UE project | P7 | Needs a checked-in project covering Actor BP / Widget BP / Enhanced Input / BlendSpace / AnimBlueprint. |
| R6 | Mac arm64 regression coverage | P7 | Preflight code detects arch mismatch; no automated regression verifies the detection on a real arm64 machine. |
| R7 | End-to-end automation for the Third Person template | P7 | Cover `IA_Crouch`, `IMC_Default`, `Locomotion`, `Crouched`, `Lean`, character Anim Class binding, compile, save, restart, restore, reload verification. |

### 3.2 Guardrail still missing

- **World Partition detection** before adding a sublevel. The post-mortem called for refusing `add_level_to_world(mode="sublevel")` on a World Partition main map. This is not yet implemented; today the handler will attempt the call regardless. Low priority until real-machine validation shows an actual failure mode.

### 3.3 Known code-side uncertainties that need a live editor to resolve

These are implemented but have engine-build-specific behaviour that pytest cannot cover; resolution is to exercise them in the real editor (see §4).

- `LevelInstanceSubsystem.create_level_instance_from` signature varies across 5.3/5.4/5.5; the handler tolerates `TypeError` with a hint but the happy path is unverified.
- `ALevelInstance` world-asset binding: either `set_world_asset(asset)` or `set_editor_property("world_asset", asset)` works on a given build. The handler tries both.
- `AutomationLibrary.take_high_res_screenshot` signature (7 vs 8 args) varies. Fallback in place.
- `RendererSettings` UPROPERTY names match UE 5.4 source; 5.5+ may rename or add properties.
- Landscape `landscape_material` property assignment timing: UE sometimes drops the material if set before the landscape is fully registered.

---

## 4. Real-Machine Test Plan

**Environment requirements**

- macOS / Windows host with UE 5.4.4 editor installed.
- A UE project on the target engine (either the `Room_w_skills` project where the original crash happened, or a fresh `Games > Third Person` template).
- `GenerativeAISupport` plugin enabled in that project.
- MCP socket server running (`python3 Content/Python/mcp_server.py` or the usual plugin startup path).
- An MCP client that can call tools and inspect structured envelopes (the `mcp_server.py` tool surface).

**Pass criteria for every test below**

- Response envelope has `success == true` (or the expected `error_code` for refusal tests).
- No fatal editor log entries (`LogWindows: Error:`, `Cast of nullptr to Actor failed`, `assertion failed`) appear in `Saved/Logs/<Project>.log` during the operation.
- Editor does not crash.
- Changes are visible in the editor and survive a save+reopen where applicable.

**Recording format per test**

Capture for each step: tool name, arguments sent, response JSON, screenshots of the editor before/after, and the tail of `Saved/Logs/<Project>.log` if anything looks wrong. File all of them under `Docs/RealMachineTests/<UE_version>/<date>/`.

---

### TC-01 Crash guardrail (highest priority)

Goal: confirm the exact command that crashed UE 5.4.4 is now refused without touching the engine.

1. Open any map.
2. Call `add_level_to_world` with:
   ```json
   {"level_path": "/Game/Maps/CodexMCP_HousePrefab",
    "mode": "sublevel",
    "streaming_class": "LevelStreamingLevelInstanceEditor"}
   ```
3. Expect `success == false`, `error_code == "LEVEL_INSTANCE_UNSAFE"`, a warning mentioning `spawn_level_instance`.
4. Confirm no `.log` entries from `UEditorLevelUtils::AddLevelToWorld`.
5. Repeat via `execute_python` with a raw script containing `add_level_to_world_with_transform(..., unreal.LevelStreamingLevelInstanceEditor, ...)`. Expect it to be refused by `utils/safety.DESTRUCTIVE_SUBSTRINGS`.

### TC-02 `get_all_scene_objects`

1. Open a populated map.
2. Call `get_all_scene_objects`.
3. Expect `success == true`, `data.count > 0`, and each entry contains `name`, `class`, `label`, `location`.
4. Confirm the previous error `type object 'EditorLevelLibrary' has no attribute 'get_level'` does not appear.

### TC-03 `create_level_from_template`

Runs:

- `{"level_path": "/Game/Tests/MCP_Basic", "template": "Basic"}`
- `{"level_path": "/Game/Tests/MCP_Empty", "template": "empty"}`
- `{"level_path": "/Game/Tests/MCP_Explicit", "template": "/Engine/Maps/Templates/Template_Default"}`
- Bad template: `{"level_path": "/Game/Tests/MCP_Bogus", "template": "Bogus"}` → expect `LEVEL_TEMPLATE_NOT_FOUND`.

Success path: verify the asset opens, save it, reopen, confirm no errors.

### TC-04 `create_level_instance_from_selection`

1. Open a saved map, spawn a few simple meshes.
2. Select them all in the Outliner.
3. Call `create_level_instance_from_selection` with `{"output_level_path": "/Game/Tests/MCP_PrefabFromSelection"}`.
4. Expect `success == true`, `data.level_instance_actor` non-empty, and a new map asset on disk.
5. Negative: call with empty selection → `LEVEL_SELECTION_EMPTY`.
6. Negative: call with `actor_names` containing a name that doesn't exist → `ACTOR_NOT_FOUND`.
7. If the engine build rejects the subsystem call, expect `LEVEL_OPERATION_FAILED` with a remediation hint (not a hard crash).

### TC-05 `spawn_level_instance` + `list_level_instances`

1. Using the asset from TC-04, open a different map.
2. Call `spawn_level_instance` with `{"level_asset_path": "/Game/Tests/MCP_PrefabFromSelection", "location": [0,0,0], "actor_label": "MCP_Prefab_1"}`.
3. Expect the actor appears in the viewport referencing the prefab.
4. Call `list_level_instances` → expect `data.count >= 1`, entry includes `label` and `world_asset`.
5. Negative: pass a non-existent asset path → `ASSET_NOT_FOUND`.

### TC-06 `add_level_to_world` (safe modes)

1. On a non-World-Partition main map, call:
   - `{"level_path": "/Game/Tests/MCP_PrefabFromSelection", "mode": "sublevel"}` → expect sublevel appears under Levels panel.
   - `{"level_path": "/Game/Tests/MCP_PrefabFromSelection", "mode": "level_instance"}` → expect a level-instance actor in the viewport.
2. Negative: `{"mode": "instanced"}` → `LEVEL_MODE_UNSUPPORTED`.
3. Negative: `{"level_path": "/Game/Missing"}` → `ASSET_NOT_FOUND`.

### TC-07 Landscape

1. Open a Basic map.
2. Call `create_landscape` with `{"location":[0,0,0], "actor_label":"MCP_Ground", "material_path":"/Engine/EngineMaterials/DefaultMaterial"}`.
3. Expect a `Landscape` actor spawns with the provided label.
4. Call `set_landscape_material` with a different valid material path. Expect the material to re-assign.
5. Negative: set a non-existent material path → `MATERIAL_NOT_FOUND`.
6. Negative: `set_landscape_material` on a non-landscape actor → `LANDSCAPE_OPERATION_FAILED`.

*Known quirk:* if `landscape_material` assignment is silently dropped, rerun with a small delay or re-open the editor; log the behaviour.

### TC-08 `capture_editor_viewport`

1. Open any map with visible geometry.
2. Call `capture_editor_viewport` with `{"width": 1280, "height": 720, "filename": "mcp_tc08"}`.
3. Expect `data.path` points under `<Project>/Saved/Screenshots`, the file exists on disk, and `data.image_base64` decodes to a valid PNG that visually matches the viewport.
4. Negative: patch project to a read-only screenshots dir → `VIEWPORT_CAPTURE_FAILED`.
5. Confirm no full-desktop capture happens (file size is consistent with the viewport, not the monitor).

### TC-09 Project settings + rendering defaults

1. Call `set_project_setting` with `{"settings_class":"/Script/Engine.RendererSettings", "key":"bDefaultFeatureBloom", "value": false}`.
2. Expect `data.saved_to_config == true`.
3. Confirm the corresponding INI entry (`Config/DefaultEngine.ini` → `[/Script/Engine.RendererSettings]` → `bDefaultFeatureBloom=False`).
4. Restart the editor; confirm the setting persists.
5. Call `set_rendering_defaults` with `{"auto_exposure": false, "motion_blur": false, "bloom": true}` → expect partial changes list with all three entries marked `ok`.
6. Negative: `{"settings_class":"/Script/Engine.DoesNotExist", "key":"bFoo", "value": true}` → `PROJECT_SETTING_NOT_FOUND`.

### TC-10 Batch actor ops

1. In a test map, spawn 4 simple `StaticMeshActor`s labelled `Wall_01..04`.
2. `duplicate_actors` with `{"actor_names":["Wall_01","Wall_02"], "offset":[200,0,0]}` → expect 2 duplicates created.
3. `replace_static_mesh` with `{"actor_names":["Wall_01"], "mesh_path":"/Engine/BasicShapes/Cube"}` → confirm mesh changes in viewport.
4. `replace_material` with `{"actor_names":["Wall_01"], "material_path":"/Engine/EngineMaterials/DefaultMaterial", "slot_index": 0}` → confirm material changes.
5. `group_actors` with `{"actor_names":["Wall_01","Wall_02"], "group_name":"Walls/North"}` → confirm Outliner folder path.
6. `select_actors` with `{"query":"Wall_0", "match":"prefix"}` → expect 4 actors selected.
7. Negative on each: bad name → `ACTOR_NOT_FOUND`; missing mesh/material → `MESH_NOT_FOUND`/`MATERIAL_NOT_FOUND`.

### TC-11 Protocol + preflight sanity

1. `get_capabilities` → verify `engine_version`, `platform`, `machine_architecture`, `input_system`, `unsafe_commands`, `api_version`.
2. `preflight_project` on (a) a healthy project and (b) a project with the plugin DLL built for a different architecture. Expect `ARCH_MISMATCH` with a remediation hint in case (b). **This is where Mac arm64 regression (open item R6) is actually exercised.**
3. `get_editor_context` → verify `editor_binary_architecture`, `project_target_architecture`, `input_system`, `dirty_assets`, `open_asset_paths`.

### TC-12 Job model

1. Kick off a long job via `execute_python` (`wait_for_completion=false`).
2. Expect immediate response with `job_id`.
3. Poll via `get_job_status(job_id)` until `status == "complete"`.
4. Start another long job and `cancel_job(job_id)`; confirm it reports cancellation.
5. `list_active_jobs` at each point for consistency.

### TC-13 Safe mutation + undo

1. `preview_operation` on any high-risk write → expect `transaction_id`, `preview` description, no mutation yet.
2. `apply_operation(transaction_id)` → expect `changed_assets`, `saved_assets`, `verification_checks` populated.
3. `undo_last_mcp_operation` → expect the change reverts cleanly and `rollback_performed == true`.
4. Force a failure (e.g. invalid preview payload) → confirm no orphan assets.

### TC-14 Session restore

1. Open several asset editors (Blueprint, AnimBlueprint, Widget, Material, BlendSpace).
2. `capture_editor_session` → confirm snapshot contains all of them plus current map + selected actors.
3. `request_editor_restart` → editor relaunches.
4. On relaunch, expect previously open assets reopen automatically (first-stage restore).
5. Confirm `restored_assets` / `failed_assets` reported correctly.

### TC-15 Input, BlendSpace, AnimBlueprint smoke

Exercise the stacked locomotion workflow from the improvement plan §P7:

- `create_input_action("IA_Crouch", "Button")`, `create_input_mapping_context("IMC_Default")`, `map_enhanced_input_action` binding.
- `get_blend_space_info("/Game/.../Locomotion")`, `set_blend_space_axis`, `replace_blend_space_samples`.
- `get_anim_blueprint_structure`, `create_state_machine("ABP_...","Locomotion")`, `create_state("Crouched")`, `create_transition`, `set_transition_rule`.
- `compile_blueprint_with_diagnostics` on the AnimBlueprint and confirm zero errors.
- Restart editor → confirm all changes persisted.

### TC-16 Regression on `Room_w_skills` (reproduces original crash scenario)

Final regression on the original project:

1. Open `/Game/Maps/CodexMCP_HouseShowcase`.
2. Run TC-01 (crash guardrail) exactly matching the failing line from `script_114c0c53b4cc4894b850cb396e7ebd0f.py`. Expect refusal.
3. Run TC-04 + TC-05 to achieve the original workflow goal (make the house reusable as a level instance) via the safe path.
4. Save, restart, reopen, confirm the showcase map references the prefab correctly.

---

## 5. Priority order for real-machine testing

1. **TC-01, TC-16** — crash-guardrail smoke. If these fail, stop and fix before any other run.
2. **TC-02, TC-11** — core inspection + preflight.
3. **TC-04, TC-05, TC-06, TC-07, TC-08** — the actual post-mortem tools.
4. **TC-09, TC-10** — project settings + batch actor ops.
5. **TC-03, TC-12, TC-13, TC-14, TC-15** — broader roadmap verification.

---

## 6. Exit criteria

Real-machine validation is considered complete when:

- Every TC in §4 is executed on UE 5.4.4 with evidence (response JSON + editor screenshot + log tail) archived under `Docs/RealMachineTests/5.4.4/<date>/`.
- Every TC passes its pass criteria, OR failures are documented with engine-level repro and filed back into this doc.
- TC-11 additionally passes on a Mac arm64 host (closes open item R6).
- TC-15 closes open item R7 (Third-Person E2E).
- The remaining open items (R1, R2, R3, R4, R5) have tracking issues with explicit owners, or are explicitly deprioritised in writing.

---

## 7. Appendix — Original crash evidence

Kept from the post-mortem for future reference:

- Failing script: `Saved/Temp/PythonExec/script_114c0c53b4cc4894b850cb396e7ebd0f.py`
- Relevant line: `stream = level_utils.add_level_to_world_with_transform(world, '/Game/Maps/CodexMCP_HousePrefab', unreal.LevelStreamingLevelInstanceEditor, transform)`
- Crash context: `~/Library/Application Support/Epic/UnrealEngine/5.4/Saved/Crashes/CrashReport-UE-Room_w_skills-pid-83509-79D859E07C43BDB550ADDD9D3CCA8F69/CrashContext.runtime-xml`
- Fatal message: `Cast of nullptr to Actor failed`
- Call stack through `ULevelInstanceSubsystem::RegisterLoadedLevelStreamingLevelInstanceEditor` → `ULevelStreamingLevelInstanceEditor::OnLevelLoadedChanged` → `UEditorLevelUtils::AddLevelToWorld`.

Maps generated in that session and kept as test fixtures:

- `/Game/Maps/CodexMCP_HousePrefab`
- `/Game/Maps/CodexMCP_HouseShowcase`
