"""Blender node-graph construction helpers.

These functions run INSIDE Blender's Python environment (bpy).
They build shader node trees based on the wiring spec from the JSON manifest.
"""

import bpy


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
):
    """Wire texture nodes into a material based on the manifest spec.

    Args:
        mat: Blender material
        textures: Dict from manifest — slot -> {path, colorspace, wiring}
        bsdf_overrides: Optional dict of Principled BSDF input overrides
        color_tints: Optional dict of color tint params from material props
                     e.g. {"RColorTint": [R, G, B, A], ...}
    """
    bsdf, output = clear_material_nodes(mat)
    tree = mat.node_tree
    nodes = tree.nodes
    links = tree.links

    if color_tints is None:
        color_tints = {}

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
        tex_path = tex_info["path"]
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
            elif target in bsdf.inputs:
                if target not in wired_inputs:
                    links.new(tex_node.outputs["Color"], bsdf.inputs[target])
                    wired_inputs.add(target)
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
                elif target in bsdf.inputs:
                    if target not in wired_inputs:
                        links.new(sep_node.outputs[ch_output], bsdf.inputs[target])
                        wired_inputs.add(target)

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
            links.new(base_source, mix_node.inputs[6])
        # B = white (default 1,1,1,1)
        mix_node.inputs[7].default_value = (1.0, 1.0, 1.0, 1.0)

        # Disconnect any existing Base Color link then wire mix result
        for link in list(tree.links):
            if link.to_node == bsdf and link.to_socket.name == "Base Color":
                tree.links.remove(link)
        links.new(mix_node.outputs[2], bsdf.inputs["Base Color"])

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

            links.new(base_color_tex_node.outputs["Color"], mix_node.inputs[6])  # A
            links.new(sep_node.outputs[ch_output], mix_node.inputs[7])            # B
            links.new(mix_node.outputs[2], bsdf.inputs["Base Color"])             # Result

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
            links.new(current_color_output, mix_node.inputs[6])
            # B = tint color (RGB from props, ignore A)
            mix_node.inputs[7].default_value = (
                tint_rgba[0], tint_rgba[1], tint_rgba[2], 1.0
            )

            current_color_output = mix_node.outputs[2]  # Result

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
            if input_name in bsdf.inputs:
                bsdf.inputs[input_name].default_value = value
