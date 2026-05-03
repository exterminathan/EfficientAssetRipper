"""Blender node-graph construction helpers.

These functions run INSIDE Blender's Python environment (bpy).
They build shader node trees based on the wiring spec from the JSON manifest.
"""

import os

import bpy


# Image-file extensions we'll feed to bpy.data.images.load.
# Anything outside this set is rejected with a status warning so an
# unexpected manifest can't make Blender try to load arbitrary files.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".exr",
               ".hdr", ".bmp", ".webp", ".dds"}


# Blender 4.x renamed several Principled BSDF input sockets.  When a manifest
# (or a user's texture_presets.json) still uses the legacy name, translate it
# to the current one rather than silently dropping the override.
BSDF_RENAMES = {
    "Subsurface": "Subsurface Weight",
    "Specular": "Specular IOR Level",
    "Transmission": "Transmission Weight",
    "Sheen": "Sheen Weight",
    "Clearcoat": "Coat Weight",
    "ClearcoatRoughness": "Coat Roughness",
    "Clearcoat Roughness": "Coat Roughness",
}


def _resolve_bsdf_input_name(bsdf, name):
    """Return the actual BSDF input name to use, applying legacy renames.

    Returns ``None`` if no socket matches even after the rename table — the
    caller should surface a warning rather than KeyError.
    """
    if name in bsdf.inputs:
        return name
    renamed = BSDF_RENAMES.get(name)
    if renamed and renamed in bsdf.inputs:
        return renamed
    return None


def _mix_input_a(mix_node):
    """Return the 'A' (first colour) input of a Mix (RGBA) node, by name."""
    return mix_node.inputs["A"]


def _mix_input_b(mix_node):
    """Return the 'B' (second colour) input of a Mix (RGBA) node, by name."""
    return mix_node.inputs["B"]


def _mix_output_result(mix_node):
    """Return the 'Result' (Color) output of a Mix (RGBA) node, by name."""
    return mix_node.outputs["Result"]


def clear_material_nodes(mat: bpy.types.Material):
    """Remove all nodes except the Material Output and Principled BSDF."""
    mat.use_nodes = True
    tree = mat.node_tree
    nodes = tree.nodes

    # Find or create essentials
    output_node = None
    bsdf_node = None

    for node in nodes:
        if node.type == "OUTPUT_MATERIAL":
            output_node = node
        elif node.type == "BSDF_PRINCIPLED":
            bsdf_node = node

    # Remove everything else
    to_remove = [n for n in nodes if n not in (output_node, bsdf_node)]
    for n in to_remove:
        nodes.remove(n)

    # Create if missing
    if output_node is None:
        output_node = nodes.new("ShaderNodeOutputMaterial")
        output_node.location = (400, 0)

    if bsdf_node is None:
        bsdf_node = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf_node.location = (0, 0)

    # Ensure BSDF -> Output link
    tree.links.new(bsdf_node.outputs["BSDF"], output_node.inputs["Surface"])

    return bsdf_node, output_node


def setup_material_textures(
    mat: bpy.types.Material,
    textures: dict,
    bsdf_overrides: dict | None = None,
    color_tints: dict | None = None,
    on_warning=None,
):
    """Wire texture nodes into a material based on the manifest spec.

    Args:
        mat: Blender material
        textures: Dict from manifest — slot -> {path, colorspace, wiring}
        bsdf_overrides: Optional dict of Principled BSDF input overrides
        color_tints: Optional dict of color tint params from material props
                     e.g. {"RColorTint": [R, G, B, A], ...}
        on_warning: Optional callable(str); called for non-fatal anomalies
                    (unknown BSDF override, unsupported texture extension).
    """
    bsdf, output = clear_material_nodes(mat)
    tree = mat.node_tree
    nodes = tree.nodes
    links = tree.links

    if color_tints is None:
        color_tints = {}

    def _warn(message: str):
        if on_warning is not None:
            try:
                on_warning(message)
            except Exception:
                pass

    # Track node positions
    x_offset = -800
    y_offset = 300
    y_step = -300

    # Base color texture node reference (for AO mixing and mask tinting)
    base_color_tex_node = None
    ao_mask_tex_node = None             # deferred AOMasks connection
    ao_channel_infos: list[tuple] = []  # deferred AO connections
    mask_tint_infos: list[tuple] = []   # deferred RGB mask tint connections
    wired_inputs: set[str] = set()      # track which BSDF inputs are already wired

    slot_index = 0

    for slot_name, tex_info in textures.items():
        raw_tex_path = tex_info["path"]
        # Normalize the case + separator so duplicate paths resolve to the
        # same image data-block in Blender's library, and so the suffix check
        # below isn't fooled by mixed-case extensions on Windows.
        tex_path = os.path.normcase(os.path.normpath(raw_tex_path))
        ext = os.path.splitext(tex_path)[1].lower()
        if ext not in _IMAGE_EXTS:
            _warn(
                f"Skipping texture with unsupported extension {ext!r}: "
                f"{raw_tex_path}"
            )
            continue

        colorspace = tex_info.get("colorspace", "sRGB")
        wiring = tex_info.get("wiring", {})
        wiring_type = wiring.get("type", "direct")

        # Create Image Texture node (label = filename, not slot name)
        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.location = (x_offset, y_offset + slot_index * y_step)

        # Load image
        img = bpy.data.images.load(tex_path, check_existing=True)
        img.colorspace_settings.name = colorspace
        tex_node.image = img

        if wiring_type == "direct":
            target = wiring.get("target_input", "Base Color")
            if target == "SKIP":
                pass  # Import texture node but don't wire (e.g. ID masks, height maps)
            elif target == "BASE_COLOR_MIX" and wiring.get("method") == "ao_multiply":
                ao_channel_infos.append((tex_node, "Color"))
            else:
                resolved = _resolve_bsdf_input_name(bsdf, target)
                if resolved is None:
                    _warn(
                        f"Material {mat.name!r}: BSDF input {target!r} not "
                        f"found (Blender version may have renamed it)"
                    )
                elif resolved not in wired_inputs:
                    links.new(tex_node.outputs["Color"], bsdf.inputs[resolved])
                    wired_inputs.add(resolved)
            if slot_name in ("base_color", "alb"):
                base_color_tex_node = tex_node

        elif wiring_type == "ao_mask":
            # Defer: needs base_color_tex_node to be set first
            ao_mask_tex_node = tex_node

        elif wiring_type == "normal_map":
            if "Normal" in wired_inputs:
                slot_index += 1
                continue  # Only wire the first normal map
            nm_node = nodes.new("ShaderNodeNormalMap")
            nm_node.location = (x_offset + 300, y_offset + slot_index * y_step)
            nm_node.label = "Normal Map"
            links.new(tex_node.outputs["Color"], nm_node.inputs["Color"])
            links.new(nm_node.outputs["Normal"], bsdf.inputs["Normal"])
            wired_inputs.add("Normal")

        elif wiring_type == "split_channels":
            sep_node = nodes.new("ShaderNodeSeparateColor")
            sep_node.location = (x_offset + 300, y_offset + slot_index * y_step)
            sep_node.label = f"{slot_name} Split"
            links.new(tex_node.outputs["Color"], sep_node.inputs["Color"])

            channels = wiring.get("channels", {})
            for ch_key, ch_info in channels.items():
                ch_output = {"R": "Red", "G": "Green", "B": "Blue"}.get(ch_key)
                if ch_output is None:
                    continue

                target = ch_info.get("target_input", "")
                method = ch_info.get("method", "")

                if target == "BASE_COLOR_MIX" and method == "ao_multiply":
                    # Defer AO connections until base color is set up
                    ao_channel_infos.append((sep_node, ch_output))
                elif target.startswith("NORMAL_"):
                    # Packed normal reconstruction — skip for now, complex
                    pass
                else:
                    resolved = _resolve_bsdf_input_name(bsdf, target)
                    if resolved is None:
                        _warn(
                            f"Material {mat.name!r}: BSDF input {target!r} not "
                            f"found (Blender version may have renamed it)"
                        )
                    elif resolved not in wired_inputs:
                        links.new(sep_node.outputs[ch_output], bsdf.inputs[resolved])
                        wired_inputs.add(resolved)

        elif wiring_type == "rgb_mask_tint":
            # Separate RGB channels of mask texture, each channel drives a
            # Mix Color node that blends its tint colour onto the base colour.
            sep_node = nodes.new("ShaderNodeSeparateColor")
            sep_node.location = (x_offset + 300, y_offset + slot_index * y_step)
            sep_node.label = f"{slot_name} Mask Split"
            links.new(tex_node.outputs["Color"], sep_node.inputs["Color"])

            channels = wiring.get("channels", {})
            for ch_key, ch_info in channels.items():
                ch_output = {"R": "Red", "G": "Green", "B": "Blue"}.get(ch_key)
                if ch_output is None:
                    continue
                tint_param = ch_info.get("tint_param", "")
                if tint_param:
                    mask_tint_infos.append((sep_node, ch_output, tint_param))

        slot_index += 1

    # Wire AO mask (AOMasks texture — Factor of a Mix node, A=base color, B=white)
    if ao_mask_tex_node is not None:
        base_source = None
        if base_color_tex_node is not None:
            base_source = base_color_tex_node.outputs["Color"]
        else:
            # Fall back to whatever is already wired into Base Color
            for link in tree.links:
                if link.to_node == bsdf and link.to_socket.name == "Base Color":
                    base_source = link.from_socket
                    break

        mix_node = nodes.new("ShaderNodeMix")
        mix_node.data_type = "RGBA"
        mix_node.blend_type = "MULTIPLY"
        mix_node.location = (
            ao_mask_tex_node.location.x + 300,
            ao_mask_tex_node.location.y - 150,
        )
        mix_node.label = "AO Mask Mix"

        # Factor = AOMasks texture
        links.new(ao_mask_tex_node.outputs["Color"], mix_node.inputs["Factor"])
        # A = base color texture output
        if base_source is not None:
            links.new(base_source, _mix_input_a(mix_node))
        # B = white (default 1,1,1,1)
        _mix_input_b(mix_node).default_value = (1.0, 1.0, 1.0, 1.0)

        # Disconnect any existing Base Color link then wire mix result
        for link in list(tree.links):
            if link.to_node == bsdf and link.to_socket.name == "Base Color":
                tree.links.remove(link)
        links.new(_mix_output_result(mix_node), bsdf.inputs["Base Color"])

    # Wire AO multiply nodes
    for sep_node, ch_output in ao_channel_infos:
        if base_color_tex_node is not None:
            mix_node = nodes.new("ShaderNodeMix")
            mix_node.data_type = "RGBA"
            mix_node.blend_type = "MULTIPLY"
            mix_node.location = (
                base_color_tex_node.location.x + 300,
                base_color_tex_node.location.y - 150,
            )
            mix_node.label = "AO Multiply"

            # Factor = 1 (full multiply)
            mix_node.inputs["Factor"].default_value = 1.0

            links.new(base_color_tex_node.outputs["Color"], _mix_input_a(mix_node))   # A
            links.new(sep_node.outputs[ch_output], _mix_input_b(mix_node))            # B
            links.new(_mix_output_result(mix_node), bsdf.inputs["Base Color"])        # Result

    # Wire RGB mask tint chain — each mask channel mixes a tint onto base color
    if mask_tint_infos:
        # Start from whatever is currently feeding Base Color.
        # If no base-color texture exists, use the ColorTint from the props
        # as the base colour (the "black" / unmasked region colour).
        if base_color_tex_node is not None:
            current_color_output = base_color_tex_node.outputs["Color"]
            ref_x = base_color_tex_node.location.x
            ref_y = base_color_tex_node.location.y
        else:
            base_tint = color_tints.get("ColorTint")
            default_rgb = nodes.new("ShaderNodeRGB")
            if base_tint:
                default_rgb.outputs["Color"].default_value = (
                    base_tint[0], base_tint[1], base_tint[2], 1.0
                )
                default_rgb.label = "ColorTint (Base)"
            else:
                default_rgb.outputs["Color"].default_value = tuple(
                    bsdf.inputs["Base Color"].default_value
                )
                default_rgb.label = "Default Base Color"
            default_rgb.location = (x_offset, y_offset + slot_index * y_step)
            current_color_output = default_rgb.outputs["Color"]
            ref_x = default_rgb.location.x
            ref_y = default_rgb.location.y

        # Check if AO already wired something into Base Color
        for link in tree.links:
            if (link.to_node == bsdf
                    and link.to_socket.name == "Base Color"):
                current_color_output = link.from_socket
                break

        mix_x = ref_x + 600
        mix_y = ref_y

        for i, (sep_node, ch_output, tint_param) in enumerate(mask_tint_infos):
            tint_rgba = color_tints.get(tint_param)
            if tint_rgba is None:
                continue  # No tint defined for this channel — skip

            mix_node = nodes.new("ShaderNodeMix")
            mix_node.data_type = "RGBA"
            mix_node.blend_type = "MIX"
            mix_node.location = (mix_x + i * 250, mix_y - i * 100)
            mix_node.label = f"Tint {tint_param}"

            # Factor = mask channel value
            links.new(sep_node.outputs[ch_output], mix_node.inputs["Factor"])
            # A = current accumulated color
            links.new(current_color_output, _mix_input_a(mix_node))
            # B = tint color (RGB from props, ignore A)
            _mix_input_b(mix_node).default_value = (
                tint_rgba[0], tint_rgba[1], tint_rgba[2], 1.0
            )

            current_color_output = _mix_output_result(mix_node)

        # Final chain result → Base Color
        # Remove existing Base Color links first
        for link in list(tree.links):
            if (link.to_node == bsdf
                    and link.to_socket.name == "Base Color"):
                tree.links.remove(link)
        links.new(current_color_output, bsdf.inputs["Base Color"])

    # Apply BSDF overrides (e.g., Transmission for glass)
    if bsdf_overrides:
        for input_name, value in bsdf_overrides.items():
            resolved = _resolve_bsdf_input_name(bsdf, input_name)
            if resolved is None:
                _warn(
                    f"Material {mat.name!r}: unknown BSDF override "
                    f"{input_name!r} (no matching socket on this Blender "
                    f"version)"
                )
                continue
            bsdf.inputs[resolved].default_value = value
