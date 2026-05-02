# Note: This guide is not for humans, LLMs can refer it to understand how to use the Unreal Engine MCP Plugin.

# Unreal Engine MCP Plugin Guide

This plugin lets an LLM (like you, Claude) boss around Unreal Engine with MCP. Automates Blueprint wizard-ry, node hookups, all that jazz. Check this s to not f it up.

## Key Notes

- **Fresh Session Rule**: Call `get_capabilities` or `preflight_project` before doing real work in a new editor session. Use that output to detect unsafe commands, missing plugins, editor path issues, and `x64`/`arm64` mismatches first.
- **Structured Results**: Prefer the structured envelope fields over ad-hoc string parsing. Read `success`, `message`, `data`, `error`, `error_code`, `warnings`, `job_id`, and `api_version` before deciding what to do next.
- **Viewport Capture**: Use `capture_editor_viewport` for images. `take_editor_screenshot` is a compatibility wrapper and returns the same structured envelope instead of raw image bytes.
- **Long-Running Work**: If a tool returns `pending=True` with a `job_id`, do not assume the command finished. Poll `get_job_status`, use `list_active_jobs` for visibility, and only call `cancel_job` when cancellation is still safe.
- **Unsafe Escape Hatch**: Treat `execute_python_script` as an unsafe fallback, especially when `get_capabilities` marks `execute_python` as unsafe. Prefer dedicated Blueprint, component, plugin, and project tools when they exist.
- **Pin Connections**: For inbuilt Events like BeginPlay Use "then" for execution pin, not "OutputDelegate" (delegates). Verify pin names in JSON.
- **Node Types**: Use `add_node_to_blueprint` with "EventBeginPlay", "Multiply_FloatFloat", or basics like "Branch", "Sequence". Unrecognized types return suggestions.
- **Node Spacing**: Set `node_position` in JSON (e.g., [0, 0], [400, 0])—maintain 400x, 300y gaps to prevent overlap.
- **Inputs**: Use `add_input_binding` to set up the binding (e.g., "Jump", "SpaceBar"), then `add_node_to_blueprint` with "K2Node_InputAction" and `"action_name": "Jump"` in `node_properties`. Ensure `action_name` matches.
- **Colliders**: Add via `add_component_with_events` (e.g., "MyBox", "BoxComponent")—returns `"begin_overlap_guid"` (BeginOverlap node) and `"end_overlap_guid"` (EndOverlap node).
- **Materials**: Use `edit_component_property` with property_name as "Material", "SetMaterial", or "BaseMaterial" and value as a material path (e.g., "'/Game/Materials/M_MyMaterial'") to set on mesh components (slot 0 default)."
- **Project Plugins**: Use `enable_plugin` to update the current project's `.uproject` plugin list. If it reports a restart requirement, follow with `restart_editor` or use `enable_plugin_and_restart`.
- **Editor Restarts**: `restart_editor` is coordinated by the external MCP process, not by Unreal itself. If Unreal reports dirty maps/assets, it should return a confirmation-required message so you can ask the user before retrying with `force=True`.



*(Additional quirks will be added as discovered.)*
