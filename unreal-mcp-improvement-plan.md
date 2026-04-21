# Unreal MCP Reliability, Protocol, And Animation Workflow Improvement Plan

## Document Info
- Version: `v1.2`
- Date: `2026-04-19` (status update: `2026-04-21`)
- Scope: Current `GenerativeAISupport` Unreal MCP plugin, Python socket server, Blueprint/AnimBlueprint/BlendSpace/Enhanced Input workflows, and MCP tool contract
- Goal: Address protocol inconsistency, architecture mismatches, missing restart session restoration, unsafe asset writes, weak ordinary Blueprint reliability, limited AnimBlueprint support, and weak diagnostics

## Status (2026-04-21)
- **P0A – P5 complete in code.** Shared response envelope, `get_capabilities`, `preflight_project`, job model, transactions/preview/apply/rollback/undo, Blueprint graph inspection & compile diagnostics, session capture/restore, Enhanced Input, BlendSpace read/write, and AnimBlueprint read/write are all implemented and covered by pytest.
- **P6 partially complete.** Unified error codes, log deltas, and mutation reports are in place. Asset diff summaries and `Saved/MCP` diagnostic bundles are still TODO.
- **P7 partially complete.** Protocol-contract, Blueprint, BlendSpace, AnimBlueprint, session, and input unit tests all pass (226 tests on `python3.11 -m pytest tests/python/`). A dedicated minimal regression project, Mac arm64 verification, and the Third-Person end-to-end flow still require real-editor validation.
- **Postmortem follow-up (this pass):** all eight gaps from `unreal-mcp-level-instance-postmortem.md` are implemented behind the standard handler pattern. See the updated postmortem doc for status per item.

## Background
The current MCP already supports basic asset creation, ordinary Blueprint graph edits, Python execution, plugin enable/disable, and editor restart. The main issue is not command availability. The issue is that many commands do not execute a full Unreal Editor transaction, and the client-facing MCP surface is still inconsistent.

This plan is grounded in the current implementation:
- Transport and dispatch: [unreal_socket_server.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/unreal_socket_server.py>)
- MCP entrypoint: [mcp_server.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/mcp_server.py>)
- Editor context and restart: [plugin_commands.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/handlers/plugin_commands.py>)
- Blueprint graph editing: [GenBlueprintNodeCreator.cpp](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Source/GenerativeAISupportEditor/Private/MCP/GenBlueprintNodeCreator.cpp>)
- Blueprint connections: [GenBlueprintUtils.cpp](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Source/GenerativeAISupportEditor/Private/MCP/GenBlueprintUtils.cpp>)
- Legacy input binding path: [basic_commands.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/handlers/basic_commands.py>)
- Python escape hatch: [python_commands.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/handlers/python_commands.py>)
- Restart launcher: [restart_editor_launcher.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/restart_editor_launcher.py>)

## Current Problems
- MCP tool responses are inconsistent. Some tools return structured dicts, while others flatten results into ad-hoc strings, which makes capability growth brittle.
- No preflight checks for module architecture, target architecture, plugin state, required editor scripting dependencies, or input system.
- The socket layer still depends on a fixed queue and a hardcoded wait timeout instead of a proper job lifecycle.
- Restart only exits and relaunches the editor. It does not preserve or restore open asset editors, active graph focus, or broader working context.
- High-risk asset writes do not use a shared transaction layer. BlendSpace and AnimBlueprint changes can leave internal caches out of sync.
- Ordinary Blueprint graph operations are still unreliable. Node lookup, pin compatibility, getters/setters, and compile diagnostics are not strong enough to support dependable automation.
- Blueprint graph lookup is too narrow. It mostly assumes `UbergraphPages`, `FunctionGraphs`, and `MacroGraphs`, which is insufficient for AnimBlueprint state machines and other graph domains.
- Input commands still prefer legacy `InputSettings`, which does not align with UE5 template projects using Enhanced Input.
- There is no first-class preview/apply/undo model. Unsafe mutations happen too early and rollback is incomplete.
- Error reporting is shallow. It often stops at Python exceptions instead of exposing structured diagnostics, changed assets, rollback state, compile errors, or Unreal log deltas.

## Goals
- Add a stable, versioned MCP response contract for all tools.
- Add reliable preflight checks, capability negotiation, and job-based execution.
- Upgrade from property-level edits to Unreal Editor transaction-level operations with preview, apply, undo, and rollback semantics.
- Stabilize ordinary Blueprint read/write workflows before expanding deeper AnimBlueprint write support.
- Add first-class APIs for BlendSpace, AnimBlueprint, and Enhanced Input.
- Make editor restart and editor navigation session-aware so working context can be restored.
- Improve observability so failures can be tied to save, compile, cache sync, pin compatibility, or architecture causes.

## Design Principles
- Python handles protocol, argument validation, orchestration, and result normalization.
- C++ Editor utilities handle actual Unreal asset mutation and graph inspection.
- The MCP protocol surface is part of the product and must be versioned, typed, and stable.
- High-risk assets use semantic APIs instead of relying on arbitrary Python execution.
- All write operations require validation, reporting, and rollback where appropriate.
- Read support comes before write support for any graph or asset domain.
- Hidden auto-fixes are discouraged. Repairs should be explicit operations or surfaced as warnings with evidence.

## Target Architecture
- `mcp_server.py`: MCP registration, protocol adaptation, typed response normalization, and client-facing tool documentation.
- `unreal_socket_server.py`: request dispatch, job lifecycle, timeout handling, cancellation, progress, and command-scoped diagnostics capture.
- `handlers/`: request normalization, capability gating, routing, and mutation report assembly.
- C++ MCP utilities: asset transactions, Blueprint core utilities, graph read/write, input utilities, animation utilities, session restore, and editor navigation/focus helpers.
- `Saved/MCP`: session snapshots, mutation reports, rollback metadata, preview artifacts, and diagnostic bundles.
- Shared response envelope for all tools: `success`, `message`, `data`, `error`, `error_code`, `warnings`, `job_id`, `changed_assets`, `api_version`.

## Delivery Phases

| Phase | Name | Goal | Output | Exit Criteria |
|---|---|---|---|---|
| P0A | Protocol Contract | Make the client surface stable | Shared response envelope, `api_version`, structured tool results | No tool relies on bespoke response parsing or string-only success paths |
| P0B | Execution Model And Preflight | Detect root causes before execution and remove fixed-timeout assumptions | Capability negotiation, project preflight, richer editor context, jobs | Architecture and plugin issues are reported before execution and long-running commands do not depend on a fixed 10s wait |
| P1 | Safe Mutation Runtime | Standardize mutation flow and make writes reversible | Transaction utilities, preview/apply, undo hooks, rollback, post-save verification | High-risk writes fail cleanly without leaving partially broken assets |
| P1.25 | Blueprint Core Reliability | Stabilize ordinary Blueprint workflows before animation-specific expansion | Graph schema inspection, pin compatibility, node resolution, compile diagnostics | Standard Blueprint add/connect/compile flows are reliable on template projects |
| P1.5 | Restart Session And Editor Focus Restore | Make restart resumable and restore context | Session snapshot, asset editor restoration, graph focus restore | Open assets and primary working context are restored after restart |
| P2 | Input System Alignment | Match UE5 defaults | Enhanced Input APIs | Input mapping works directly in template projects |
| P3 | BlendSpace Support | Stabilize animation sample editing | Read APIs and safe write APIs | BlendSpace changes save and reload correctly |
| P4 | AnimBlueprint Read Support | Understand structures before mutating | State machine and graph introspection | MCP can accurately describe existing AnimBlueprints |
| P5 | AnimBlueprint Write Support | Enable semantic animation graph editing | State machine and transition APIs | Locomotion-oriented AnimBlueprint edits are supported safely |
| P6 | Cross-Cutting Observability | Make failures diagnosable across all domains | Error taxonomy, mutation reports, log deltas, asset diff summaries | Failures include enough information to locate the cause without reproducing blindly |
| P7 | Testing And Validation | Add regression protection | Test project, protocol checks, end-to-end scripts | Core workflows pass on template projects and Mac arm64 |

## Detailed Development Checklist

### P0A Protocol Contract
- [x] Add a shared response envelope to all tool paths.
  - Files: [mcp_server.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/mcp_server.py>), Python handlers
  - Standard fields: `success`, `message`, `data`, `error`, `error_code`, `warnings`, `job_id`, `changed_assets`, `api_version`.

- [x] Normalize all MCP tool functions to return structured results first and human-readable summaries second.
  - Files: [mcp_server.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/mcp_server.py>)
  - Remove special-case parsing where a caller has to guess whether a tool returned a dict, JSON string, or plain string.

- [x] Add `get_capabilities`.
  - Files: [unreal_socket_server.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/unreal_socket_server.py>), [mcp_server.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/mcp_server.py>)
  - Must return: `engine_version`, `platform`, `machine_architecture`, `input_system`, `supported_asset_types`, `supported_graph_types`, `unsafe_commands`, `api_version`.

- [x] Mark unsafe paths explicitly in capabilities and tool docs.
  - Files: [mcp_server.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/mcp_server.py>)
  - Minimum unsafe set: `execute_python`, raw AnimBlueprint graph mutation, force restart without save.

### P0B Execution Model And Preflight
- [x] Add `preflight_project`.
  - Files: new `preflight_commands.py`, [plugin_commands.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/handlers/plugin_commands.py>)
  - Must check: `.uproject`, editor executable, project target architecture, binary module architecture, plugin state, editor scripting dependencies.
  - Must explicitly report `x64/arm64` mismatch with a remediation hint.

- [x] Expand `get_editor_context`.
  - Files: [plugin_commands.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/handlers/plugin_commands.py>)
  - Add: `editor_binary_architecture`, `project_target_architecture`, `module_architectures`, `input_system`, `enabled_plugins`, `dirty_assets`, `open_asset_paths`.

- [x] Replace the fixed queue/timeout flow with a job model.
  - Files: [unreal_socket_server.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/unreal_socket_server.py>)
  - Add: `job_id`, job states, progress, cancellation, per-command timeout policies.

- [x] Add job lifecycle APIs.
  - Files: [unreal_socket_server.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/unreal_socket_server.py>), [mcp_server.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/mcp_server.py>)
  - Provide: `get_job_status`, `cancel_job`, `list_active_jobs`.

- [x] Capture command-scoped Unreal log snapshots for every job.
  - Files: shared logging utilities, handlers
  - Foundational diagnostics land here rather than waiting until the end of the roadmap.

### P1 Safe Mutation Runtime
- [x] Add `UGenAssetTransactionUtils`.
  - Files: new `GenAssetTransactionUtils.cpp/.h`
  - Standard flow: `Load -> Preview -> Modify -> Apply -> PostEdit sync -> Save/Compile -> Reload -> Verify`.

- [x] Add preview/apply APIs for high-risk writes.
  - Files: `GenAssetTransactionUtils`, Python handler orchestration
  - Provide: `preview_operation`, `apply_operation`.
  - Preview should describe target assets, intended changes, and validation blockers before mutation.

- [x] Add rollback support for high-risk asset types.
  - Files: `GenAssetTransactionUtils`, Python handler orchestration
  - Strategy: duplicate temporary asset, validate changes on the duplicate, then replace the primary asset only after success.

- [x] Add undo-oriented hooks for MCP-originated operations where Unreal transaction support allows it.
  - Files: `GenAssetTransactionUtils`, editor utility helpers
  - Provide at minimum: `undo_last_mcp_operation` or equivalent transaction handle flow.

- [x] Downgrade `execute_python` to an explicitly unsafe escape hatch.
  - Files: [python_commands.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/handlers/python_commands.py>)
  - Add: `read_only`, `dry_run`, `changed_assets`, `recent_logs`, `dirty_packages`.

### P1.25 Blueprint Core Reliability
- [x] Replace narrow graph lookup with full graph enumeration for core Blueprint utilities, not only AnimBlueprint work.
  - Files: [GenBlueprintNodeCreator.cpp](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Source/GenerativeAISupportEditor/Private/MCP/GenBlueprintNodeCreator.cpp>), [GenBlueprintUtils.cpp](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Source/GenerativeAISupportEditor/Private/MCP/GenBlueprintUtils.cpp>)
  - Use `UBlueprint::GetAllGraphs()` and graph path resolution instead of only scanning `UbergraphPages`, `FunctionGraphs`, and `MacroGraphs`.

- [x] Add Blueprint graph inspection APIs.
  - Files: `GenBlueprintUtils`, new or existing Blueprint handler files
  - Provide: `get_graph_schema`, `get_graph_nodes`, `get_graph_pins`, `resolve_graph_by_path`.

- [x] Add stronger node and pin resolution APIs.
  - Files: `GenBlueprintNodeCreator.cpp`, `GenBlueprintUtils.cpp`
  - Provide: `resolve_node_by_selector`, `get_pin_compatibility`, `suggest_autocast_path`.

- [x] Strengthen connection diagnostics.
  - Files: `GenBlueprintUtils.cpp`
  - On failure, return exact pin names, directions, categories, subtypes, and why the connection is invalid.

- [x] Return real compile diagnostics instead of boolean-only success.
  - Files: `GenBlueprintUtils.cpp`, Blueprint handlers
  - Must return compile status, warnings, and errors with graph or node context where available.

- [x] Remove or downgrade hidden compile-time repairs.
  - Files: `GenBlueprintUtils.cpp`
  - Silent auto-fixes should either become explicit repair operations or be surfaced as warnings in the mutation report.

### P1.5 Restart Session And Editor Focus Restore
- [x] Add `capture_editor_session`.
  - Files: new `session_commands.py`, new `GenEditorSessionUtils.cpp/.h`
  - Capture: `open_asset_paths`, `primary_asset_path`, `active_graph_path`, `selected_actors`, `current_map`, `selected_nodes`.

- [x] Capture open asset editors through `UAssetEditorSubsystem->GetAllEditedAssets()`.
  - Files: `GenEditorSessionUtils`
  - Save to `Saved/MCP/LastEditorSession.json`.

- [x] Save the session snapshot automatically before restart.
  - Files: [plugin_commands.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/handlers/plugin_commands.py>)
  - Hook this into `request_editor_restart`.

- [x] Restore the last editor session after relaunch.
  - Files: `init_unreal.py`, `session_commands.py`, `GenEditorSessionUtils`
  - First-stage restore targets: `Blueprint`, `AnimBlueprint`, `Animation Sequence`, `BlendSpace`, `Widget Blueprint`, `Material`.

- [x] Restore the primary working asset, graph, and node focus.
  - Files: `GenEditorSessionUtils`
  - The main asset should be focused after restoration, and graph focus should be restored when possible.

- [x] Add explicit editor navigation helpers.
  - Files: `GenEditorSessionUtils`, Python handlers
  - Provide: `open_asset`, `bring_asset_to_front`, `focus_graph`, `focus_node`, `select_actor`.

- [x] Add restore fault tolerance.
  - Files: `GenEditorSessionUtils`, Python handlers
  - Return `restored_assets` and `failed_assets`. One failure must not stop the rest.

- [ ] Add a restore policy setting.
  - Files: plugin settings, handlers
  - Modes: `none`, `assets_only`, `assets_and_tabs`
  - Default: `assets_only`

- [ ] Add second-stage tab/layout restoration.
  - Files: `GenEditorSessionUtils`
  - Restore common tabs such as Content Browser, World Outliner, Details, Output Log.
  - Defer this until asset restoration is already stable.

### P2 Input System Alignment
- [x] Add `UGenEnhancedInputUtils` and `input_commands.py`.
  - Provide: `create_input_action`, `create_input_mapping_context`, `map_enhanced_input_action`, `list_input_mappings`.

- [x] Keep legacy `add_input_binding`, but warn in Enhanced Input projects.
  - Files: [basic_commands.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/handlers/basic_commands.py>)

### P3 BlendSpace Support
- [x] Add `get_blend_space_info`.
  - Files: new `animation_commands.py`, new `GenAnimationAssetUtils.cpp/.h`
  - Must return: axis settings, sample list, target skeleton, additive settings.

- [x] Add safe write APIs for BlendSpace.
  - Files: `GenAnimationAssetUtils`
  - Provide: `set_blend_space_axis`, `replace_blend_space_samples`, `set_blend_space_sample_animation`.
  - Internally call `ResampleData`, `ValidateSampleData`, and `PostEditChange`.

- [x] Add post-save reload verification for BlendSpace.
  - Files: `GenAnimationAssetUtils`
  - Reload and compare sample count, coordinates, and asset references after save.

### P4 AnimBlueprint Read Support
- [x] Add `get_anim_blueprint_structure`.
  - Files: new `GenAnimationBlueprintUtils.cpp/.h`, new `animation_commands.py`
  - Must expose: state machines, states, transitions, aliases, cached poses, slots, state asset bindings.

- [x] Add AnimBlueprint-specific graph inspection APIs.
  - Files: `GenAnimationBlueprintUtils`
  - Provide: `get_graph_nodes`, `get_graph_pins`, `resolve_graph_by_path`.

- [x] Add stable selectors for animation structures.
  - Files: `GenAnimationBlueprintUtils`
  - Provide: state machine path selectors, state selectors, transition selectors, and alias selectors.

### P5 AnimBlueprint Write Support
- [x] Add semantic state machine APIs.
  - Files: `GenAnimationBlueprintUtils`, `animation_commands.py`
  - Provide: `create_state_machine`, `create_state`, `create_transition`, `set_transition_rule`, `create_state_alias`, `set_alias_targets`.

- [x] Add state content APIs.
  - Files: `GenAnimationBlueprintUtils`
  - Provide: `set_state_sequence_asset`, `set_state_blend_space_asset`, `set_cached_pose_node`, `set_default_slot_chain`, `set_apply_additive_chain`.

- [x] Make semantic AnimBlueprint APIs the default path.
  - Files: `GenBlueprintUtils.cpp`, `GenBlueprintNodeCreator.cpp`, Python handlers
  - Raw node editing for AnimBlueprint should be `unsafe`, not the primary route.

### P6 Cross-Cutting Observability
- [x] Return structured mutation reports for all write operations.
  - Fields: `changed_assets`, `compiled_assets`, `saved_assets`, `warnings`, `rollback_performed`, `verification_checks`.

- [x] Attach Unreal log deltas automatically when save or compile fails.
  - Files: [python_commands.py](</Users/Shared/Epic Games/UE_5.4/Engine/Plugins/Marketplace/GenerativeAISupport/Content/Python/handlers/python_commands.py>), shared logging utilities

- [x] Add unified error codes.
  - Minimum set: `ARCH_MISMATCH`, `PROTOCOL_SHAPE_MISMATCH`, `JOB_TIMEOUT`, `PIN_INCOMPATIBLE`, `NODE_NOT_FOUND`, `GRAPH_NOT_SUPPORTED`, `ASSET_VALIDATION_FAILED`, `SAVE_FAILED`, `ROLLBACK_FAILED`, `LEGACY_INPUT_PATH`, `UNSAFE_COMMAND_REQUIRED`.

- [ ] Add asset diff summaries for write operations where comparison is feasible.
  - Files: transaction utilities, diagnostics utilities

- [ ] Persist diagnostic bundles to `Saved/MCP`.
  - Include: request payload, normalized arguments, mutation report, log delta, and verify results.

### P7 Testing And Validation
- [ ] Create a minimal regression project.
  - Cover: Actor Blueprint, Widget Blueprint, Enhanced Input, BlendSpace, AnimBlueprint.

- [x] Add protocol contract regression checks.
  - Validate response envelope shape, `api_version`, and job lifecycle responses.

- [x] Add Blueprint core reliability regression coverage.
  - Cover: node add, node connect, getter/setter nodes, compile diagnostics, and graph path resolution.

- [ ] Add Mac arm64 regression coverage.
  - Validate project preflight and architecture mismatch detection.

- [ ] Add end-to-end automation for the Third Person template.
  - Cover: `IA_Crouch`, `IMC_Default`, `Locomotion`, `Crouched`, `Lean`, character Anim Class binding, compile, save, restart, restore, and reload verification.

## Priority Order
1. `P0A` Protocol Contract
2. `P0B` Execution Model And Preflight
3. `P1` Safe Mutation Runtime
4. `P1.25` Blueprint Core Reliability
5. `P1.5` Restart Session And Editor Focus Restore
6. `P2` Input System Alignment
7. `P3` BlendSpace Support
8. `P4` AnimBlueprint Read Support
9. `P5` AnimBlueprint Write Support
10. `P6` Cross-Cutting Observability
11. `P7` Testing And Validation

## Milestones
- Milestone 1: Every MCP tool returns a stable structured result and environment issues are reported before execution.
- Milestone 2: Long-running commands support jobs, progress, and cancellation instead of fixed blocking timeouts.
- Milestone 3: High-risk writes use preview/apply/rollback semantics and can be reversed cleanly.
- Milestone 4: Ordinary Blueprint edits are reliable, diagnosable, and no longer depend on hidden repair behavior.
- Milestone 5: Restart restores previously open working assets and primary editor focus automatically.
- Milestone 6: BlendSpace writes complete safely and survive save plus reload.
- Milestone 7: The MCP can reliably inspect existing AnimBlueprint structures.
- Milestone 8: The MCP can safely perform semantic locomotion edits in AnimBlueprints.

## Non-Goals
- Full arbitrary graph mutation for every editor graph type is not a first-phase goal.
- `execute_python` is not the primary implementation path.
- Full Slate layout fidelity is deferred until asset editor restoration is stable.
- Preserving ad-hoc string response shapes for new APIs is not a goal.

## Suggested Sprint Breakdown
- Sprint 1: `P0A + P0B`
- Sprint 2: `P1 + P1.25`
- Sprint 3: `P1.5 + P2 + P3`
- Sprint 4: `P4 + P5`
- Sprint 5: `P6 + P7`

## Execution Plan Files
- Foundation slice (`P0A + P0B`): [2026-04-19-unreal-mcp-foundation-plan.md](/Users/zonglindi/Documents/UnrealGenAISupport/docs/superpowers/plans/2026-04-19-unreal-mcp-foundation-plan.md:1)
- Next recommended plan: safe mutation runtime plus Blueprint core reliability (`P1 + P1.25`)
- Then: restart/session restore plus input alignment plus BlendSpace (`P1.5 + P2 + P3`)
- Then: AnimBlueprint read/write (`P4 + P5`)
- Final stabilization: observability plus validation (`P6 + P7`)
