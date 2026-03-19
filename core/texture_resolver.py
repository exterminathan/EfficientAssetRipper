"""Classify textures by suffix and resolve them to on-disk TGA paths.

Uses the wiring presets from texture_presets.json and the Everything SDK
to turn a list of texture names (from a material's props.txt) into a dict
mapping each slot to an absolute file path + colorspace.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.everything import EverythingSDK


@dataclass
class ResolvedTexture:
    """A texture that has been classified and located on disk."""
    slot: str              # e.g. "base_color", "orm", "normal"
    texture_name: str      # original name from props file
    path: Path             # absolute path to TGA on disk
    colorspace: str        # "sRGB" or "Non-Color"
    wiring: dict           # wiring spec from presets JSON


@dataclass
class UnresolvedTexture:
    """A texture that could not be classified or found on disk."""
    texture_name: str
    reason: str            # "no_matching_suffix" or "file_not_found"


@dataclass
class TextureResolution:
    """Full resolution result for one material."""
    resolved: list[ResolvedTexture]
    unresolved: list[UnresolvedTexture]
    preset_used: str


def _pick_closest_path(candidates: list[Path], reference: Path) -> Path:
    """Pick the candidate sharing the longest common path prefix with reference."""
    if len(candidates) == 1:
        return candidates[0]

    ref_parts = reference.parts
    best = candidates[0]
    best_score = 0
    for c in candidates:
        score = 0
        for a, b in zip(c.parts, ref_parts):
            if a.lower() == b.lower():
                score += 1
            else:
                break
        if score > best_score:
            best_score = score
            best = c
    return best


def classify_texture(
    texture_name: str, texture_slots: dict, param_name: str = "",
) -> Optional[tuple[str, dict]]:
    """Determine which slot a texture name belongs to.

    Checks suffix matching first, then falls back to matching the
    ParameterInfo Name from the props file against param_names lists.

    Returns (slot_name, slot_config) or None if nothing matches.
    """
    name_upper = texture_name.rstrip("_").upper()
    # Pass 1: suffix matching
    for slot_name, slot_cfg in texture_slots.items():
        for suffix in slot_cfg.get("suffixes", []):
            if name_upper.endswith(suffix.upper()):
                return slot_name, slot_cfg
    # Pass 2: param_name matching (e.g. ParameterInfo Name = "BaseColor")
    if param_name:
        for slot_name, slot_cfg in texture_slots.items():
            for pn in slot_cfg.get("param_names", []):
                if param_name == pn:
                    return slot_name, slot_cfg
    return None


def resolve_textures(
    texture_names: list[str],
    presets_data: dict,
    sdk: EverythingSDK,
    preset_name: str = "default_pbr",
    material_name: str = "",
    reference_path: Optional[Path] = None,
    game_folder: str = "",
    param_name_map: Optional[dict[str, str]] = None,
) -> TextureResolution:
    """Resolve a list of texture names to classified, located files.

    Args:
        texture_names: Base names from material props (e.g. ["XWing_01_Droid_C", ...])
        presets_data: Full parsed texture_presets.json
        sdk: Everything SDK instance
        preset_name: Which preset to use
        material_name: Material name for checking overrides
        reference_path: Path of the source PSK, used for closest-match logic
        game_folder: Scope Everything searches to this folder
        param_name_map: Optional dict mapping texture_name -> ParameterInfo Name
    """
    resolved: list[ResolvedTexture] = []
    unresolved: list[UnresolvedTexture] = []

    if param_name_map is None:
        param_name_map = {}

    # Global ignore list (exact name) and ignore patterns (substring)
    ignore_textures = set(
        n.upper() for n in presets_data.get("ignore_textures", [])
    )
    ignore_patterns = [
        p.upper() for p in presets_data.get("ignore_patterns", [])
    ]

    # Check for per-material override
    overrides = presets_data.get("material_overrides", {})
    if material_name in overrides:
        override = overrides[material_name]
        preset_name = override.get("preset", preset_name)

    preset = presets_data.get("presets", {}).get(preset_name)
    if not preset:
        for tn in texture_names:
            unresolved.append(UnresolvedTexture(tn, f"preset '{preset_name}' not found"))
        return TextureResolution(resolved, unresolved, preset_name)

    texture_slots = preset["texture_slots"]

    # If override forces specific textures, apply those
    force_textures: dict[str, str] = {}
    if material_name in overrides:
        force_textures = overrides[material_name].get("force_textures", {})

    # Track which slots we've already filled
    filled_slots: set[str] = set()

    # First pass: resolve forced textures from overrides
    for slot_name, forced_name in force_textures.items():
        if slot_name in texture_slots:
            slot_cfg = texture_slots[slot_name]
            candidates = sdk.find_texture(forced_name, folder=game_folder)
            if candidates:
                chosen = _pick_closest_path(candidates, reference_path or Path())
                resolved.append(ResolvedTexture(
                    slot=slot_name,
                    texture_name=forced_name,
                    path=chosen,
                    colorspace=slot_cfg.get("colorspace", "sRGB"),
                    wiring=slot_cfg.get("wiring", {}),
                ))
                filled_slots.add(slot_name)
            else:
                unresolved.append(UnresolvedTexture(forced_name, "file_not_found"))

    # Second pass: classify and resolve remaining textures
    for tex_name in texture_names:
        # Skip globally ignored texture names (exact or substring)
        name_up = tex_name.upper()
        if name_up in ignore_textures:
            continue
        if any(pat in name_up for pat in ignore_patterns):
            continue

        pname = param_name_map.get(tex_name, "")
        result = classify_texture(tex_name, texture_slots, param_name=pname)
        if result is None:
            unresolved.append(UnresolvedTexture(tex_name, "no_matching_suffix"))
            continue

        slot_name, slot_cfg = result

        if slot_name in filled_slots:
            continue  # Override already filled this slot

        candidates = sdk.find_texture(tex_name, folder=game_folder)
        if not candidates:
            unresolved.append(UnresolvedTexture(tex_name, "file_not_found"))
            continue

        chosen = _pick_closest_path(candidates, reference_path or Path())
        resolved.append(ResolvedTexture(
            slot=slot_name,
            texture_name=tex_name,
            path=chosen,
            colorspace=slot_cfg.get("colorspace", "sRGB"),
            wiring=slot_cfg.get("wiring", {}),
        ))
        filled_slots.add(slot_name)

    return TextureResolution(resolved, unresolved, preset_name)
