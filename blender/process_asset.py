"""Blender headless processing script.

Launched via:  blender --background --python process_asset.py -- manifest.json

Reads a JSON manifest, imports PSK/PSKX, wires materials, saves .blend.
Prints structured JSON status lines to stdout for the GUI to parse.
"""

import json
import os
import sys
import traceback

# ---------------------------------------------------------------------------
# Parse args — everything after "--" belongs to us
# ---------------------------------------------------------------------------
argv = sys.argv
if "--" in argv:
    argv = argv[argv.index("--") + 1:]
else:
    argv = []

if not argv:
    print(json.dumps({"status": "error", "message": "No manifest path provided"}))
    sys.exit(1)

manifest_path = argv[0]

with open(manifest_path, "r", encoding="utf-8") as f:
    manifest = json.load(f)


def status_msg(status: str, **kwargs):
    """Print a JSON status line to stdout."""
    msg = {"status": status, **kwargs}
    print("##ASSET_STATUS##" + json.dumps(msg), flush=True)


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

import bpy  # noqa: E402  (only available inside Blender)

# Add parent dir to path so we can import material_setup
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from material_setup import setup_material_textures  # noqa: E402


def main():
    psk_path = manifest["psk_path"]
    output_path = manifest["output_path"]
    addon_name = manifest.get("addon_name", "bl_ext.blender_org.io_scene_psk_psa")
    materials_spec = manifest.get("materials", {})

    status_msg("started", asset=os.path.basename(psk_path))

    # 1. Enable PSK import addon (before any scene reset so extension system is intact)
    try:
        bpy.ops.preferences.addon_enable(module=addon_name)
        status_msg("progress", step="addon_enabled")
    except Exception as e:
        status_msg("warning", message=f"Could not enable addon {addon_name}: {e}")

    # 2. Clean scene — remove all objects, meshes, materials, etc.
    #    (Don't use read_factory_settings as it wipes the extension system)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)
    for img in list(bpy.data.images):
        bpy.data.images.remove(img)
    for arm in list(bpy.data.armatures):
        bpy.data.armatures.remove(arm)

    # 3. Import PSK/PSKX
    try:
        ext = os.path.splitext(psk_path)[1].lower()
        if ext in (".psk", ".pskx"):
            bpy.ops.psk.import_file(filepath=psk_path)
        else:
            status_msg("error", message=f"Unsupported file type: {ext}")
            sys.exit(1)
        status_msg("progress", step="psk_imported")
    except Exception as e:
        status_msg("error", message=f"PSK import failed: {e}")
        sys.exit(1)

    # 4. Wire materials
    processed_materials = 0
    failed_materials = 0

    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue

        for mat_slot in obj.material_slots:
            mat = mat_slot.material
            if mat is None:
                continue

            mat_name = mat.name

            # Find matching material spec in manifest
            # Try exact match on slot name first, then material name
            mat_spec = None
            for slot_name, spec in materials_spec.items():
                if slot_name == mat_name or spec.get("material_name") == mat_name:
                    mat_spec = spec
                    break

            if mat_spec is None:
                status_msg("warning", message=f"No texture spec for material: {mat_name}")
                continue

            textures = mat_spec.get("textures", {})
            bsdf_overrides = mat_spec.get("bsdf_overrides", {})
            color_tints = mat_spec.get("color_tints", {})

            if not textures:
                continue

            try:
                setup_material_textures(mat, textures, bsdf_overrides, color_tints)
                processed_materials += 1
                status_msg("progress", step="material_wired", material=mat_name)
            except Exception as e:
                failed_materials += 1
                status_msg("warning", message=f"Failed to wire {mat_name}: {e}")
                traceback.print_exc()

    # 5. Remove armature, keep mesh, clean up names
    for obj in list(bpy.data.objects):
        if obj.type == "ARMATURE":
            # Unparent children first (keep transform)
            for child in list(obj.children):
                child.parent = None
                child.matrix_world = child.matrix_world  # preserve world transform
            bpy.data.objects.remove(obj, do_unlink=True)
    for arm in list(bpy.data.armatures):
        bpy.data.armatures.remove(arm)

    # Strip trailing .001 / .002 etc. from object and mesh names
    for obj in bpy.data.objects:
        import re as _re
        cleaned = _re.sub(r"\.\d{3}$", "", obj.name)
        if cleaned != obj.name:
            obj.name = cleaned
        if obj.data and hasattr(obj.data, "name"):
            data_cleaned = _re.sub(r"\.\d{3}$", "", obj.data.name)
            if data_cleaned != obj.data.name:
                obj.data.name = data_cleaned

    # 6. Set viewport clip start/end + focus on object
    # In headless (--background) mode bpy.context.screen.areas is empty,
    # so we set clip values via bpy.data.screens which persists into the
    # saved .blend regardless of whether we're headless or interactive.
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.clip_start = 0.1
                        space.clip_end = 100000

    if not bpy.app.background:

        bpy.ops.object.select_all(action="DESELECT")
        mesh_obj = None
        for obj in bpy.data.objects:
            if obj.type == "MESH":
                obj.select_set(True)
                mesh_obj = obj
        if mesh_obj:
            bpy.context.view_layer.objects.active = mesh_obj
            for area in bpy.context.screen.areas:
                if area.type == "VIEW_3D":
                    with bpy.context.temp_override(area=area):
                        bpy.ops.view3d.view_selected()

    # 7. Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 8. Save .blend
    try:
        bpy.ops.wm.save_as_mainfile(filepath=output_path)
        status_msg("progress", step="saved")
    except Exception as e:
        status_msg("error", message=f"Failed to save: {e}")
        sys.exit(1)

    status_msg(
        "completed",
        asset=os.path.basename(psk_path),
        materials_processed=processed_materials,
        materials_failed=failed_materials,
    )


try:
    main()
except Exception as e:
    status_msg("error", message=str(e))
    traceback.print_exc()
    sys.exit(1)
