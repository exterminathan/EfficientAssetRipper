"""Blender headless script: combine multiple .blend files into one.

Launched via:
    blender --background --python combine_blends.py -- manifest.json

Manifest JSON format:
{
    "blend_files": ["path/a.blend", "path/b.blend", ...],
    "output_path": "path/combined.blend",
    "spacing": 5.0
}

Objects from each file are appended and offset along the X axis by `spacing`
units per file, so they're laid out in a grid.
"""

import json
import math
import os
import sys

argv = sys.argv
if "--" in argv:
    argv = argv[argv.index("--") + 1:]
else:
    argv = []

if not argv:
    print(json.dumps({"status": "error", "message": "No manifest path provided"}))
    sys.exit(1)

with open(argv[0], "r", encoding="utf-8") as f:
    manifest = json.load(f)

import bpy  # noqa: E402


def status(msg_type: str, **kw):
    print("##COMBINE_STATUS##" + json.dumps({"status": msg_type, **kw}), flush=True)


def main():
    blend_files: list[str] = manifest["blend_files"]
    output_path: str = manifest["output_path"]
    spacing: float = manifest.get("spacing", 5.0)
    columns: int = manifest.get("columns", 0)

    if not blend_files:
        status("error", message="No .blend files provided")
        sys.exit(1)

    status("started", total=len(blend_files))

    # Clean scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    # Grid layout: use square-ish grid if columns=0
    n = len(blend_files)
    if columns <= 0:
        columns = max(1, math.isqrt(n))

    for i, blend_path in enumerate(blend_files):
        if not os.path.isfile(blend_path):
            status("warning", message=f"File not found, skipping: {blend_path}")
            continue

        col = i % columns
        row = i // columns
        offset_x = col * spacing
        offset_y = -(row * spacing)

        # Append all objects from this .blend
        try:
            with bpy.data.libraries.load(blend_path, link=False) as (src, dst):
                dst.objects = src.objects

            appended = []
            for obj in dst.objects:
                if obj is not None:
                    bpy.context.collection.objects.link(obj)
                    appended.append(obj)

            # Offset all appended objects
            for obj in appended:
                obj.location.x += offset_x
                obj.location.y += offset_y

            status(
                "progress",
                file=os.path.basename(blend_path),
                index=i + 1,
                total=n,
                objects=len(appended),
            )
        except Exception as e:
            status("warning", message=f"Failed to load {blend_path}: {e}")

    # Apply viewport clip start/end so the combined scene opens correctly.
    # Uses bpy.data.screens (works in headless mode) so values persist.
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.clip_start = 0.1
                        space.clip_end = 100000

    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=output_path)

    total_objs = len(bpy.data.objects)
    status("completed", output=output_path, total_objects=total_objs)


try:
    main()
except Exception as e:
    status("error", message=str(e))
    import traceback
    traceback.print_exc()
    sys.exit(1)
