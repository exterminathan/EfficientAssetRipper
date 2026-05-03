"""Blender headless script: combine multiple .blend files into one.

Launched via:
    blender --background --python combine_blends.py -- manifest.json [--nonce <hex>]

Manifest JSON format:
{
    "blend_files": ["path/a.blend", "path/b.blend", ...],
    "output_path": "path/combined.blend",
    "spacing": 5.0
}

Objects from each file are appended and offset along the X axis by `spacing`
units per file, so they're laid out in a grid.
"""

import argparse
import json
import math
import os
import re
import sys

argv = sys.argv
if "--" in argv:
    argv = argv[argv.index("--") + 1:]
else:
    argv = []

_parser = argparse.ArgumentParser(prog="combine_blends.py", add_help=False)
_parser.add_argument("manifest", nargs="?", help="Path to JSON manifest")
_parser.add_argument("--nonce", default="", help="Status-prefix nonce")
_args, _unknown = _parser.parse_known_args(argv)

_NONCE_RE = re.compile(r"^[0-9a-fA-F]{8,64}$")
NONCE = _args.nonce if _args.nonce and _NONCE_RE.match(_args.nonce) else ""
STATUS_PREFIX = f"##COMBINE_STATUS:{NONCE}##" if NONCE else "##COMBINE_STATUS##"


def status(msg_type: str, **kw):
    print(STATUS_PREFIX + json.dumps({"status": msg_type, **kw}), flush=True)


if not _args.manifest:
    status("error", message="No manifest path provided")
    sys.exit(1)

with open(_args.manifest, "r", encoding="utf-8") as f:
    manifest = json.load(f)

# Validate output path before doing any expensive work.
_output_path = manifest.get("output_path", "")
if not isinstance(_output_path, str) or not _output_path:
    status("error", message="Manifest missing output_path")
    sys.exit(1)
if not os.path.isabs(_output_path):
    status("error", message=f"output_path must be absolute: {_output_path}")
    sys.exit(1)
if os.path.splitext(_output_path)[1].lower() != ".blend":
    status("error", message=f"output_path must end in .blend: {_output_path}")
    sys.exit(1)

import bpy  # noqa: E402


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

    appended_total = 0

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

            appended_total += len(appended)

            status(
                "progress",
                file=os.path.basename(blend_path),
                index=i + 1,
                total=n,
                objects=len(appended),
            )
        except Exception as e:
            status("warning", message=f"Failed to load {blend_path}: {e}")

    if appended_total == 0:
        # Nothing was appended successfully — saving here would just persist
        # an empty (or default-cube) scene under the user's intended name.
        status(
            "error",
            message=(
                f"No objects appended from {len(blend_files)} input file(s); "
                "refusing to save an empty combined .blend."
            ),
        )
        sys.exit(1)

    # Apply viewport clip start/end so the combined scene opens correctly.
    # Uses bpy.data.screens (works in headless mode) so values persist.
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.clip_start = 0.1
                        space.clip_end = 100000

    # Save atomically — write to .tmp first, replace on success.
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp_path = output_path + ".tmp"
    try:
        bpy.ops.wm.save_as_mainfile(filepath=tmp_path)
        try:
            tmp_size = os.path.getsize(tmp_path)
        except OSError:
            tmp_size = 0
        if tmp_size <= 0:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            status("error", message="Saved combined .blend was empty (0 bytes)")
            sys.exit(1)
        os.replace(tmp_path, output_path)
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        status("error", message=f"Failed to save combined .blend: {e}")
        sys.exit(1)

    total_objs = len(bpy.data.objects)
    status("completed", output=output_path, total_objects=total_objs)


try:
    main()
except SystemExit:
    raise
except Exception as e:
    status("error", message=str(e))
    import traceback
    traceback.print_exc()
    sys.exit(1)
