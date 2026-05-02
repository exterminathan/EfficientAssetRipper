"""Parsers for UE5 exported .props.txt files (mesh and material)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MaterialRef:
    """A material reference extracted from a mesh's props.txt."""
    slot_name: str          # e.g. "FuselageShader"
    material_name: str      # e.g. "M_XWing_01_Fuselage"
    asset_path: str         # full UE asset path


@dataclass
class TextureRef:
    """A texture reference extracted from a material's props.txt."""
    texture_name: str       # e.g. "XWing_01_Droid_C"
    asset_path: str         # full UE asset path
    param_name: str = ""    # ParameterInfo Name, e.g. "BaseColor", "Normal"


@dataclass
class MeshProps:
    """Parsed contents of a mesh .props.txt file."""
    materials: list[MaterialRef] = field(default_factory=list)


@dataclass
class MaterialProps:
    """Parsed contents of a material .props.txt file."""
    textures: list[TextureRef] = field(default_factory=list)
    is_two_sided: bool = False
    is_masked: bool = False
    blend_mode: str = "BLEND_Opaque"
    color_tints: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    scalar_params: dict[str, float] = field(default_factory=dict)
    parent_name: str = ""          # parent material name for inheritance chain


# ---------------------------------------------------------------------------
# Asset path helper
# ---------------------------------------------------------------------------

# Matches patterns like:
#   Material3'/Game/.../M_Name.M_Name'
#   MaterialInstanceConstant'/Game/.../M_Name.M_Name'
#   Texture2D'/Game/.../T_Name.T_Name'
_ASSET_RE = re.compile(
    r"(?:Material\w*|Texture2D|MaterialInstanceConstant)"
    r"'(/[^']+)'"
)

# Matches Parent ObjectName like:
#   MaterialInstanceConstant'MI_Factory2D_01'
#   Material'MM_Factory_Array'
_PARENT_RE = re.compile(
    r"(?:Material\w*|MaterialInstanceConstant)"
    r"'([^']+)'"
)


_VALID_OBJ_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _extract_asset_name(asset_path: str) -> str:
    """Extract the short name from a UE asset path.

    '/Game/Foo/Bar/M_Name.M_Name'  -> 'M_Name'
    '/Game/Foo/Bar/Package.SubAsset' -> 'SubAsset'
    '/Game/Foo/Bar/MI_Beetle.0'    -> 'MI_Beetle'  (numeric export index)

    Splitting only on the *last* dot avoids mangling names that contain dots
    (rare, but seen in DLC packages). The trailing token has to look like a
    plain identifier or be a pure-ASCII digit string (export index); anything
    else is returned as-is so we never silently drop a weird suffix.
    """
    # Take the part after the last /
    tail = asset_path.rsplit("/", 1)[-1]
    if "." not in tail:
        return tail
    package, _, obj_name = tail.rpartition(".")
    # Numeric export index → fall back to package name.
    if obj_name and obj_name.isascii() and obj_name.isdigit():
        return package
    if _VALID_OBJ_NAME_RE.match(obj_name):
        return obj_name
    # Suffix doesn't look like a real identifier; return as-is so we still
    # produce *something* — silently dropping characters has bitten us before.
    return obj_name


# ---------------------------------------------------------------------------
# Mesh props parser
# ---------------------------------------------------------------------------

def parse_mesh_props(text: str, *, source: str = "<string>") -> MeshProps:
    """Parse a mesh .props.txt file and extract material references.

    Handles both JSON format (exported by CUE4Parse CLI) and the legacy
    UE text-export format.  JSON is tried first; text parsing is the fallback.

    Covers all three UE material array names:
      - SkeletalMaterials  (USkeletalMesh)
      - StaticMaterials    (UStaticMesh)
      - Materials          (older / non-typed exports)
    """
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return _parse_mesh_props_json(stripped)
        except json.JSONDecodeError as e:
            log.warning(
                "Malformed mesh-props JSON in %s line %d: %s — falling back to text parser",
                source, e.lineno, e.msg,
            )
        except Exception:
            log.exception(
                "Unexpected mesh-props JSON parse failure in %s — falling back",
                source,
            )

    return _parse_mesh_props_text(text)


# ---------------------------------------------------------------------------
# JSON parser (CUE4Parse CLI output)
# ---------------------------------------------------------------------------

def _parse_mesh_props_json(text: str) -> MeshProps:
    """Parse material refs from a JSON-format props file.

    CUE4Parse serialises the full typed UObject, so the material arrays
    (SkeletalMaterials / StaticMaterials / Materials) can appear either at
    the root of the object or nested inside a "Properties" sub-object
    depending on the game / UE version.

    SkeletalMesh:  entry["Material"]["ObjectPath"]
    StaticMesh:    entry["MaterialInterface"]["ObjectPath"]  (or "Material")
    """
    data = json.loads(text)
    # Support both a bare object and a list of export objects
    if isinstance(data, list):
        data = data[0] if data else {}

    result = MeshProps()

    # Look in root first, then inside "Properties"
    search_scopes = [data]
    props = data.get("Properties")
    if isinstance(props, dict):
        search_scopes.append(props)

    for scope in search_scopes:
        for array_name in ("SkeletalMaterials", "StaticMaterials", "Materials"):
            entries = scope.get(array_name)
            if not isinstance(entries, list) or not entries:
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                slot_name = entry.get("MaterialSlotName", "") or ""
                # SkeletalMesh uses "Material"; StaticMesh uses "MaterialInterface"
                mat_ref = entry.get("Material") or entry.get("MaterialInterface")
                if not isinstance(mat_ref, dict):
                    continue
                asset_path = mat_ref.get("ObjectPath", "") or ""
                if not asset_path:
                    continue
                material_name = _extract_asset_name(asset_path)
                if material_name:
                    result.materials.append(MaterialRef(
                        slot_name=slot_name or material_name,
                        material_name=material_name,
                        asset_path=asset_path,
                    ))
            if result.materials:
                return result

    return result


# ---------------------------------------------------------------------------
# Legacy text parser
# ---------------------------------------------------------------------------

def _parse_mesh_props_text(text: str) -> MeshProps:
    """Parse material refs from the legacy UE text-export format."""
    result = MeshProps()

    # Split into material array blocks.
    # Handles StaticMaterials[N], SkeletalMaterials[N], and Materials[N].
    blocks = _split_material_blocks(text)

    for block in blocks:
        slot_name = ""
        material_name = ""
        asset_path = ""

        for line in block.splitlines():
            line = line.strip()

            if line.startswith("MaterialSlotName"):
                _, _, val = line.partition("=")
                slot_name = val.strip()
            elif line.startswith("MaterialInterface") or line.startswith("Material "):
                m = _ASSET_RE.search(line)
                if m:
                    asset_path = m.group(1)
                    material_name = _extract_asset_name(asset_path)

        if material_name:
            result.materials.append(MaterialRef(
                slot_name=slot_name or material_name,
                material_name=material_name,
                asset_path=asset_path,
            ))

    # Fallback: bare Materials[N] = ... single-line entries
    if not result.materials:
        result.materials = _parse_materials_array(text)

    return result


def _split_material_blocks(text: str) -> list[str]:
    """Extract individual material-array entry blocks from UE text format.

    Handles StaticMaterials[N], SkeletalMaterials[N], and Materials[N],
    including both flat and nested wrapper layouts.
    """
    _MAT_ARRAY_RE = re.compile(
        r"(?:StaticMaterials|SkeletalMaterials|Materials)\[\d+\]\s*=$"
    )
    blocks: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if _MAT_ARRAY_RE.match(line):
            depth = 0
            block_lines: list[str] = []
            i += 1
            while i < len(lines):
                bl = lines[i]
                if "{" in bl:
                    depth += 1
                if "}" in bl:
                    depth -= 1
                    if depth <= 0:
                        i += 1
                        break
                block_lines.append(bl)
                i += 1
            blocks.append("\n".join(block_lines))
        else:
            i += 1

    # Handle nested wrapper format — expand any wrapper block that
    # contains inner material-array entries (e.g. StaticMaterials[6]
    # wrapping StaticMaterials[0] .. StaticMaterials[5]).
    expanded: list[str] = []
    for block in blocks:
        has_inner = any(_MAT_ARRAY_RE.match(l.strip()) for l in block.splitlines())
        if has_inner:
            inner = _split_material_blocks(block)
            if inner:
                expanded.extend(inner)
                continue
        expanded.append(block)

    return expanded


def _parse_materials_array(text: str) -> list[MaterialRef]:
    """Fallback: extract from single-line Materials[N] / StaticMaterials[N] entries."""
    refs: list[MaterialRef] = []
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"(?:StaticMaterials|SkeletalMaterials|Materials)\[\d+\]\s*=", line):
            m = _ASSET_RE.search(line)
            if m:
                asset_path = m.group(1)
                name = _extract_asset_name(asset_path)
                refs.append(MaterialRef(
                    slot_name=name,
                    material_name=name,
                    asset_path=asset_path,
                ))
    return refs


# ---------------------------------------------------------------------------
# Material props parser
# ---------------------------------------------------------------------------

def parse_material_props(text: str, *, source: str = "<string>") -> MaterialProps:
    """Parse a material .props.txt and extract texture references + flags.

    Supports both JSON format (CUE4Parse CLI output) and legacy UE text format.
    """
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return _parse_material_props_json(stripped)
        except json.JSONDecodeError as e:
            log.warning(
                "Malformed material-props JSON in %s line %d: %s — falling back to text parser",
                source, e.lineno, e.msg,
            )
        except Exception:
            log.exception(
                "Unexpected material-props JSON parse failure in %s — falling back",
                source,
            )

    return _parse_material_props_text(text)


# ---------------------------------------------------------------------------
# Material props — JSON parser
# ---------------------------------------------------------------------------

def _parse_material_props_json(text: str) -> MaterialProps:
    """Parse texture refs and material flags from JSON-format props."""
    data = json.loads(text)
    if isinstance(data, list):
        data = data[0] if data else {}

    result = MaterialProps()
    seen_textures: set[str] = set()

    # Look in root and inside "Properties"
    search_scopes = [data]
    props = data.get("Properties")
    if isinstance(props, dict):
        search_scopes.append(props)

    # Extract parent material reference
    for scope in search_scopes:
        parent_ref = scope.get("Parent")
        if isinstance(parent_ref, dict) and not result.parent_name:
            # Try ObjectName first: "MaterialInstanceConstant'MI_Factory2D_01'"
            obj_name = parent_ref.get("ObjectName", "")
            if obj_name:
                pm = _PARENT_RE.search(obj_name)
                if pm:
                    result.parent_name = _extract_asset_name(pm.group(1))
            # Fallback to ObjectPath
            if not result.parent_name:
                obj_path = parent_ref.get("ObjectPath", "")
                if obj_path:
                    result.parent_name = _extract_asset_name(obj_path)

    for scope in search_scopes:
        # TextureParameterValues — primary source of texture refs
        tex_params = scope.get("TextureParameterValues")
        if isinstance(tex_params, list):
            for entry in tex_params:
                if not isinstance(entry, dict):
                    continue
                param_info = entry.get("ParameterInfo") or {}
                param_name = param_info.get("Name", "") if isinstance(param_info, dict) else ""
                param_val = entry.get("ParameterValue")
                if not isinstance(param_val, dict):
                    continue
                # Skip Texture2DArray references (different asset type)
                obj_name = param_val.get("ObjectName", "")
                if "Texture2DArray" in obj_name:
                    continue
                asset_path = param_val.get("ObjectPath", "") or ""
                if not asset_path:
                    # Try ObjectName as fallback
                    if obj_name:
                        m = re.search(r"Texture2D'(/[^']+)'", obj_name)
                        if m:
                            asset_path = m.group(1)
                if not asset_path:
                    continue
                tex_name = _extract_asset_name(asset_path)
                if tex_name and tex_name not in seen_textures:
                    seen_textures.add(tex_name)
                    result.textures.append(TextureRef(
                        texture_name=tex_name,
                        asset_path=asset_path,
                        param_name=param_name,
                    ))

        # VectorParameterValues — color tints
        vec_params = scope.get("VectorParameterValues")
        if isinstance(vec_params, list):
            for entry in vec_params:
                if not isinstance(entry, dict):
                    continue
                param_info = entry.get("ParameterInfo") or {}
                name = param_info.get("Name", "") if isinstance(param_info, dict) else ""
                val = entry.get("ParameterValue")
                if not isinstance(val, dict) or not name:
                    continue
                try:
                    # R/G/B must be present; A defaults to 1.0 (UE often
                    # omits alpha for opaque colour tints).
                    if any(k not in val for k in ("R", "G", "B")):
                        continue
                    result.color_tints[name] = (
                        float(val["R"]),
                        float(val["G"]),
                        float(val["B"]),
                        float(val.get("A", 1.0)),
                    )
                except (ValueError, TypeError):
                    pass

        # ScalarParameterValues
        scalar_params = scope.get("ScalarParameterValues")
        if isinstance(scalar_params, list):
            for entry in scalar_params:
                if not isinstance(entry, dict):
                    continue
                param_info = entry.get("ParameterInfo") or {}
                name = param_info.get("Name", "") if isinstance(param_info, dict) else ""
                if not name:
                    continue
                try:
                    result.scalar_params[name] = float(entry.get("ParameterValue", 0))
                except (ValueError, TypeError):
                    pass

        # Boolean flags and blend mode
        if scope.get("TwoSided") is True:
            result.is_two_sided = True
        if scope.get("bIsMasked") is True:
            result.is_masked = True
        blend = scope.get("BlendMode")
        if isinstance(blend, str):
            result.blend_mode = blend

        # TextureStreamingData fallback
        if not result.textures:
            streaming = scope.get("TextureStreamingData")
            if isinstance(streaming, list):
                for entry in streaming:
                    if not isinstance(entry, dict):
                        continue
                    tex_name = entry.get("TextureName", "")
                    if tex_name and tex_name not in seen_textures:
                        seen_textures.add(tex_name)
                        result.textures.append(TextureRef(
                            texture_name=tex_name,
                            asset_path="",
                            param_name="",
                        ))

    return result


# ---------------------------------------------------------------------------
# Material props — legacy text parser
# ---------------------------------------------------------------------------

def _parse_material_props_text(text: str) -> MaterialProps:
    """Parse a material .props.txt (legacy text format) and extract texture references + flags."""
    result = MaterialProps()
    seen_textures: set[str] = set()

    # Parse VectorParameterValues for color tints (R/G/BColorTint, etc.)
    _parse_vector_params(text, result)
    # Parse ScalarParameterValues
    _parse_scalar_params(text, result)

    # Track the last seen ParameterInfo Name for associating with textures
    last_param_name = ""

    for line in text.splitlines():
        stripped = line.strip()

        # Track ParameterInfo Name (appears before ParameterValue in same block)
        if "ParameterInfo" in stripped and "Name=" in stripped:
            pm = re.search(r"Name=([^}\s]+)", stripped)
            if pm:
                last_param_name = pm.group(1).strip()

        # Texture references (skip Texture2DArray — different asset type)
        elif "Texture2DArray'" in stripped:
            pass
        elif "Texture2D'" in stripped:
            m = re.search(r"Texture2D'(/[^']+)'", stripped)
            if m:
                asset_path = m.group(1)
                tex_name = _extract_asset_name(asset_path)
                if tex_name not in seen_textures:
                    seen_textures.add(tex_name)
                    result.textures.append(
                        TextureRef(
                            texture_name=tex_name,
                            asset_path=asset_path,
                            param_name=last_param_name,
                        )
                    )
                last_param_name = ""

        # Boolean flags. Use partition+exact-compare so we don't misread
        # a value of e.g. ``TrueColor`` as boolean True.
        elif stripped.startswith("TwoSided"):
            _, _, val = stripped.partition("=")
            result.is_two_sided = val.strip().lower() == "true"
        elif stripped.startswith("bIsMasked"):
            _, _, val = stripped.partition("=")
            result.is_masked = val.strip().lower() == "true"
        elif stripped.startswith("BlendMode"):
            _, _, val = stripped.partition("=")
            result.blend_mode = val.strip()

        # Parent material reference
        elif "Parent" in stripped:
            pm = _PARENT_RE.search(stripped)
            if pm and not result.parent_name:
                result.parent_name = _extract_asset_name(pm.group(1))

    # Fallback: if no Texture2D refs found, try TextureStreamingData entries.
    # These contain bare texture names (e.g. "TextureName = Bark_C") without
    # full asset paths, but the suffix is usually enough to classify.
    if not result.textures:
        _parse_texture_streaming_data(text, result, seen_textures)

    return result


def _parse_texture_streaming_data(
    text: str, result: MaterialProps, seen_textures: set[str]
):
    """Extract texture names from TextureStreamingData blocks as fallback.

    Only the first texture per classified slot is kept (duplicates with
    different spellings are common in these blocks).
    """
    for m in re.finditer(r"TextureName\s*=\s*(\S+)", text):
        tex_name = m.group(1).strip()
        if tex_name in seen_textures:
            continue
        seen_textures.add(tex_name)
        result.textures.append(
            TextureRef(texture_name=tex_name, asset_path="", param_name="")
        )


def _parse_vector_params(text: str, result: MaterialProps):
    """Extract VectorParameterValues (color tints) from material props.

    Looks for blocks like:
        VectorParameterValues[N] =
        {
            ParameterInfo = { Name=RColorTint }
            ParameterValue = { R=0.646, G=0.433, B=0.168, A=1 }
        }

    Uses a two-pass approach: first locate the VectorParameterValues section,
    then pair each ParameterInfo Name with the ParameterValue in the same
    inner block so that scalar parameters can't bleed across.
    """
    # Find each individual VectorParameterValues[N] = { ... } inner block.
    # Channel order can vary (and A is sometimes omitted), so we parse the
    # brace block into a dict instead of locking to "R,G,B,A".
    for block_m in re.finditer(
        r"VectorParameterValues\[\d+\]\s*=\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}",
        text,
    ):
        block = block_m.group(1)
        name_m = re.search(r"ParameterInfo\s*=\s*\{\s*Name=([^}\s]+)", block)
        val_block_m = re.search(
            r"ParameterValue\s*=\s*\{([^{}]*)\}", block
        )
        if name_m and val_block_m:
            name = name_m.group(1).strip()
            channels = _parse_color_channels(val_block_m.group(1))
            if channels is not None:
                result.color_tints[name] = channels


def _parse_color_channels(text: str) -> Optional[tuple[float, float, float, float]]:
    """Parse ``R=.., G=.., B=.., [A=..]`` from a brace block in any order.

    Missing ``A`` defaults to 1.0. Returns ``None`` if R/G/B are missing or
    a value isn't parseable as a float.
    """
    pairs = re.findall(r"([RGBA])\s*=\s*([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)", text)
    if not pairs:
        return None
    by_chan: dict[str, float] = {}
    for chan, val in pairs:
        try:
            by_chan[chan] = float(val)
        except ValueError:
            return None
    if not all(c in by_chan for c in "RGB"):
        return None
    return (by_chan["R"], by_chan["G"], by_chan["B"], by_chan.get("A", 1.0))


def _parse_scalar_params(text: str, result: MaterialProps):
    """Extract ScalarParameterValues from material props.

    Uses the same block-scoped approach to avoid cross-contamination.
    """
    for block_m in re.finditer(
        r"ScalarParameterValues\[\d+\]\s*=\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}",
        text,
    ):
        block = block_m.group(1)
        name_m = re.search(r"ParameterInfo\s*=\s*\{\s*Name=([^}\s]+)", block)
        val_m = re.search(r"ParameterValue\s*=\s*([\d.eE+-]+)", block)
        if name_m and val_m:
            name = name_m.group(1).strip()
            try:
                result.scalar_params[name] = float(val_m.group(1))
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# File-level convenience
# ---------------------------------------------------------------------------

def parse_mesh_props_file(path: Path) -> MeshProps:
    # utf-8-sig strips a BOM if present — some game exports include one and
    # the leading ﻿ would otherwise break the ``startswith({|[)`` test.
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return parse_mesh_props(text, source=str(path))


def parse_material_props_file(path: Path) -> MaterialProps:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return parse_material_props(text, source=str(path))
