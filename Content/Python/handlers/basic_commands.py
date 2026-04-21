import unreal
from typing import Dict, Any, List, Tuple

import base64
import os
import mss
import time
import tempfile # Used to find the OS's temporary folder

from utils import unreal_conversions as uc
from utils import logging as log

def handle_spawn(command: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle a spawn command
    
    Args:
        command: The command dictionary containing:
            - actor_class: Actor class name/path or mesh path (e.g., "/Game/Blueprints/BP_Barrel" or "/Game/Meshes/SM_Barrel01.SM_Barrel01")
            - location: [X, Y, Z] coordinates (optional)
            - rotation: [Pitch, Yaw, Roll] in degrees (optional)
            - scale: [X, Y, Z] scale factors (optional)
            - actor_label: Optional custom name for the actor
            
    Returns:
        Response dictionary with success/failure status and additional info
    """
    try:
        # Extract parameters
        actor_class_name = command.get("actor_class", "Cube")
        location = command.get("location", (0, 0, 0))
        rotation = command.get("rotation", (0, 0, 0))
        scale = command.get("scale", (1, 1, 1))
        actor_label = command.get("actor_label")

        unreal.log(f"Spawn command: Class: {actor_class_name}, Label: {actor_label}")

        # Convert parameters to Unreal types
        loc = uc.to_unreal_vector(location)
        rot = uc.to_unreal_rotator(rotation)
        scale_vector = uc.to_unreal_vector(scale)

        actor = None
        gen_actor_utils = unreal.GenActorUtils

        # Check if it's a mesh path (e.g., "/Game/.../SM_Barrel01.SM_Barrel01")
        if actor_class_name.startswith("/Game") and "." in actor_class_name:
            # Try loading as a static mesh
            mesh = unreal.load_object(None, actor_class_name)
            if isinstance(mesh, unreal.StaticMesh):
                actor = gen_actor_utils.spawn_static_mesh_actor(actor_class_name, loc, rot, scale_vector, actor_label or "")
            else:
                # Fallback to actor class if not a mesh
                actor = gen_actor_utils.spawn_actor_from_class(actor_class_name, loc, rot, scale_vector, actor_label or "")
        else:
            # Handle basic shapes or actor classes
            shape_map = {"cube": "Cube", "sphere": "Sphere", "cylinder": "Cylinder", "cone": "Cone"}
            actor_class_lower = actor_class_name.lower()
            if actor_class_lower in shape_map:
                proper_name = shape_map[actor_class_lower]
                actor = gen_actor_utils.spawn_basic_shape(proper_name, loc, rot, scale_vector, actor_label or "")
            else:
                actor = gen_actor_utils.spawn_actor_from_class(actor_class_name, loc, rot, scale_vector, actor_label or "")

        if not actor:
            unreal.log_error(f"Failed to spawn actor of type {actor_class_name}")
            return {"success": False, "error": f"Failed to spawn actor of type {actor_class_name}"}

        actor_name = actor.get_actor_label()
        unreal.log(f"Spawned actor: {actor_name} at {loc}")
        return {"success": True, "actor_name": actor_name}

    except Exception as e:
        unreal.log_error(f"Error spawning actor: {str(e)}")
        return {"success": False, "error": str(e)}




def handle_take_screenshot(command):
    """
    Takes a screenshot using the HighResShot console command with a deterministic filename.
    This is the most reliable method for engine-based screenshots.
    """
    # 1. Create a unique, absolute file path in the OS's temp directory.
    # This ensures we have a clean place to work with guaranteed write permissions.
    temp_dir = tempfile.gettempdir()
    unique_filename = f"unreal_mcp_screenshot_{int(time.time())}.png"
    # Use forward slashes, as this is more reliable for Unreal console commands
    screenshot_path = os.path.join(temp_dir, unique_filename).replace('\\', '/')

    try:
        # 2. Construct the console command with the full, absolute filename.
        console_command = f'HighResShot 1 filename="{screenshot_path}"'
        unreal.log(f"Executing screenshot command: {console_command}")

        # Execute the command in the editor world context
        unreal.SystemLibrary.execute_console_command(unreal.EditorLevelLibrary.get_editor_world(), console_command)

        # 3. Poll for the file's existence instead of using a fixed sleep time.
        # This is much more reliable than a fixed wait.
        max_wait_seconds = 5
        wait_interval = 0.2
        time_waited = 0
        file_created = False
        while time_waited < max_wait_seconds:
            if os.path.exists(screenshot_path):
                file_created = True
                break
            time.sleep(wait_interval)
            time_waited += wait_interval

        if not file_created:
            return {"success": False, "error": f"Command was executed, but the output file was not found at the specified path: {screenshot_path}"}

        # 4. Read the file, encode it, and prepare the response
        with open(screenshot_path, 'rb') as image_file:
            image_data = image_file.read()
        base64_encoded_data = base64.b64encode(image_data).decode('utf-8')

        return {
            "success": True,
            "data": base64_encoded_data,
            "mime_type": "image/png"
        }

    except Exception as e:
        return {"success": False, "error": f"The screenshot process failed with an exception: {str(e)}"}

    finally:
        # 5. Clean up the temporary screenshot file from the temp directory
        if os.path.exists(screenshot_path):
            try:
                os.remove(screenshot_path)
            except Exception as e_cleanup:
                unreal.log_error(f"Failed to delete temporary screenshot file '{screenshot_path}': {e_cleanup}")

def handle_create_material(command: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle a create_material command
    
    Args:
        command: The command dictionary containing:
            - material_name: Name for the new material
            - color: [R, G, B] color values (0-1)
            
    Returns:
        Response dictionary with success/failure status and material path if successful
    """
    try:
        # Extract parameters
        material_name = command.get("material_name", "NewMaterial")
        color = command.get("color", (1, 0, 0))

        log.log_command("create_material", f"Name: {material_name}, Color: {color}")

        # Use the C++ utility class
        gen_actor_utils = unreal.GenActorUtils
        color_linear = uc.to_unreal_color(color)

        material = gen_actor_utils.create_material(material_name, color_linear)

        if not material:
            log.log_error("Failed to create material")
            return {"success": False, "error": "Failed to create material"}

        material_path = f"/Game/Materials/{material_name}"
        log.log_result("create_material", True, f"Path: {material_path}")
        return {"success": True, "material_path": material_path}

    except Exception as e:
        log.log_error(f"Error creating material: {str(e)}", include_traceback=True)
        return {"success": False, "error": str(e)}


def handle_get_all_scene_objects(command: Dict[str, Any]) -> Dict[str, Any]:
    """Return a structured list of actors in the current editor world.

    The previous implementation called ``EditorLevelLibrary.get_level(world)``
    which no longer exists on UE 5.4 and returned
    ``type object 'EditorLevelLibrary' has no attribute 'get_level'``.
    We now prefer the ``EditorActorSubsystem`` (UE 5.1+) and fall back to
    ``GameplayStatics.get_all_actors_of_class`` so existing callers keep
    working.
    """
    try:
        actors: List[Any] = []

        # Primary path: EditorActorSubsystem (available since UE 5.1).
        get_sub = getattr(unreal, "get_editor_subsystem", None)
        actor_sub_cls = getattr(unreal, "EditorActorSubsystem", None)
        if callable(get_sub) and actor_sub_cls is not None:
            try:
                sub = get_sub(actor_sub_cls)
                if sub is not None:
                    actors = list(sub.get_all_level_actors() or [])
            except Exception:
                actors = []

        # Fallback: GameplayStatics.get_all_actors_of_class(world, Actor).
        if not actors:
            world = None
            ued_sub_cls = getattr(unreal, "UnrealEditorSubsystem", None)
            if callable(get_sub) and ued_sub_cls is not None:
                try:
                    ued = get_sub(ued_sub_cls)
                    if ued is not None:
                        world = ued.get_editor_world()
                except Exception:
                    world = None
            if world is None:
                legacy = getattr(unreal, "EditorLevelLibrary", None)
                if legacy is not None and hasattr(legacy, "get_editor_world"):
                    try:
                        world = legacy.get_editor_world()
                    except Exception:
                        world = None
            actor_cls = getattr(unreal, "Actor", None)
            gs = getattr(unreal, "GameplayStatics", None)
            if world is not None and actor_cls is not None and gs is not None:
                try:
                    actors = list(gs.get_all_actors_of_class(world, actor_cls) or [])
                except Exception:
                    actors = []

        result = []
        for actor in actors:
            try:
                loc = actor.get_actor_location()
                entry = {
                    "name": str(actor.get_name()),
                    "label": str(actor.get_actor_label()),
                    "class": str(actor.get_class().get_name()),
                    "location": [float(loc.x), float(loc.y), float(loc.z)],
                }
            except Exception:
                continue
            result.append(entry)
        return {"success": True, "actors": result, "count": len(result)}
    except Exception as e:
        return {"success": False, "error": str(e)}

def handle_create_project_folder(command: Dict[str, Any]) -> Dict[str, Any]:
    try:
        folder_path = command.get("folder_path")
        full_path = f"/Game/{folder_path}"
        unreal.EditorAssetLibrary.make_directory(full_path)
        return {"success": True, "message": f"Created folder at {full_path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def handle_get_files_in_folder(command: Dict[str, Any]) -> Dict[str, Any]:
    try:
        folder_path = f"/Game/{command.get('folder_path')}"
        files = unreal.EditorAssetLibrary.list_assets(folder_path, recursive=False)
        return {"success": True, "files": [str(f) for f in files]}
    except Exception as e:
        return {"success": False, "error": str(e)}

def handle_add_input_binding(command: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy action-mapping path.  Enhanced Input is preferred since UE5.1."""
    try:
        action_name = command.get("action_name")
        key = command.get("key")

        # Best-effort warning: if the project already ships Enhanced Input the
        # caller should prefer ``create_input_action`` / ``map_enhanced_input_action``.
        warnings: List[Dict[str, str]] = []
        try:
            from utils.input_mapping import legacy_binding_warning
        except ImportError:  # pragma: no cover - pytest layout
            legacy_binding_warning = None
        if legacy_binding_warning is not None:
            try:
                project_uses_ei = False
                try:
                    import importlib
                    plugin_mgr = getattr(unreal, "PluginManager", None)
                    if plugin_mgr and hasattr(plugin_mgr, "get"):
                        plugins = plugin_mgr.get().get_enabled_plugins_with_content()
                        project_uses_ei = any("EnhancedInput" in p.get_name() for p in plugins)
                except Exception:
                    project_uses_ei = False
                warning = legacy_binding_warning(project_uses_ei)
                if warning is not None:
                    warnings.append(warning)
            except Exception:  # pragma: no cover - safety net
                pass

        input_settings = unreal.InputSettings.get_input_settings()
        action_mapping = unreal.InputActionKeyMapping(
            action_name=action_name, key=unreal.InputCoreTypes.get_key(key)
        )
        input_settings.add_action_mapping(action_mapping)
        input_settings.save_config()
        response: Dict[str, Any] = {
            "success": True,
            "message": f"Added input binding {action_name} -> {key}",
        }
        if warnings:
            response["warnings"] = warnings
        return response
    except Exception as e:
        return {"success": False, "error": str(e)}