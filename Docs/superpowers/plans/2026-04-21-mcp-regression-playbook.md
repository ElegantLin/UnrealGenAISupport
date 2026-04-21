# MCP Regression Playbook (P7)

This document captures the manual regression coverage expected before each
MCP release. Unit tests under `tests/python/` cover pure-Python logic; the
playbook below covers flows that require a real Unreal Editor session.

## 1. Minimal regression project

The plugin expects a companion UE project with these assets:

| Asset Path | Type | Purpose |
|------------|------|---------|
| `/Game/Regression/BP_Actor`          | Actor Blueprint      | Node add / connect / getter-setter |
| `/Game/Regression/WBP_Widget`        | Widget Blueprint     | Widget tree edits                  |
| `/Game/Regression/Input/IA_Jump`     | Input Action         | Enhanced Input creation            |
| `/Game/Regression/Input/IMC_Default` | Input Mapping Context| Enhanced Input mapping             |
| `/Game/Regression/Anim/BS_Walk`      | BlendSpace           | Axis + sample replace              |
| `/Game/Regression/Anim/ABP_Char`     | AnimBlueprint        | State machine edits                |

## 2. Protocol contract

Automated via `tests/python/test_protocol_contract.py`:

* Every response has `success: bool`, `api_version`, `warnings: list`.
* Error responses carry `error`, `error_code` (validated against
  `utils/error_codes.ALL_ERROR_CODES`).
* Handlers in `session_commands`, `input_commands`, `animation_commands`,
  and `anim_blueprint_commands` pass the empty-input sweep.

## 3. Blueprint core reliability

From inside the regression project, run (via MCP tools):

1. `spawn_blueprint_node` into `BP_Actor` event graph.
2. `connect_blueprint_nodes` between two nodes; verify
   `PIN_INCOMPATIBLE` surfaces on mismatched pin types.
3. `add_variable_with_getter_setter` for a `float` variable; confirm both
   getter and setter nodes exist via `get_graph_nodes`.
4. `compile_blueprint_with_diagnostics`; confirm structured diagnostics
   with `COMPILE_FAILED` when a pin is deliberately left dangling.

## 4. Mac arm64 preflight

On an Apple Silicon machine:

1. Call `check_architecture`; confirm response contains
   `machine_architecture: "arm64"` and no `ARCH_MISMATCH` warning when the
   editor is also arm64.
2. Launch an `x86_64` UE build and reconnect; confirm the next mutating
   call returns `error_code: ARCH_MISMATCH` before touching the editor.

## 5. Third Person end-to-end

Using a default `Third Person` template project:

1. `create_input_action` for `IA_Crouch`; map it in `IMC_Default`.
2. `create_state_machine` named `Locomotion`; add states `Idle`, `Walk`,
   `Crouched`.
3. `create_transition` from `Idle`→`Walk` with a `bool_property` rule on
   `bIsMoving`; verify the returned mutation report contains both
   `compiled_assets` and `saved_assets`.
4. `set_state_sequence_asset` for each state.
5. `bind_anim_class_on_character` and compile the Character Blueprint.
6. `save_editor_session` → `request_editor_restart` → reopen.
7. After the editor is back, call `restore_editor_session` with
   `policy="assets_and_tabs"`; verify the same tabs are reopened.
8. Re-read the AnimBlueprint structure and diff against the pre-restart
   snapshot: `state_machines_added` and `transitions_added` must be
   empty.

## 6. Diagnostic bundles

Every write response now carries a `mutation_report`. On failure (save or
compile) a diagnostic bundle is written under
`<ProjectSaved>/MCP/diagnostics/<timestamp>_<op>.json` with:

* `request_payload`
* `normalized_arguments`
* `mutation_report`
* `log_delta` (lines captured during the command)
* `verify_results`

Spot-check after any failed regression run.
