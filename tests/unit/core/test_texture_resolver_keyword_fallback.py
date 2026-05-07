"""Unit tests for the keyword auto-detect fallback in `core.texture_resolver`."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.texture_resolver import (
    _keyword_classify,
    _scan_folder_for_psk,
    resolve_textures,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _keyword_classify — pure scoring function
# ---------------------------------------------------------------------------

def test_keyword_classify_basecolor_beats_color():
    """``basecolor`` is longer and more specific than ``color`` — must win."""
    result = _keyword_classify("t_battery_basecolor")
    assert result is not None
    slot, kw, _ = result
    assert slot == "base_color"
    assert kw == "basecolor"


def test_keyword_classify_normal():
    result = _keyword_classify("t_battery_normal")
    assert result is not None
    assert result[0] == "normal"


def test_keyword_classify_returns_none_for_unmatched():
    assert _keyword_classify("t_battery_xyzqq") is None


def test_keyword_classify_picks_longest_keyword_on_collision():
    """A stem matching multiple keywords picks the longest match."""
    # "occlusion" (9) beats "_ao" (3) within the AO slot's keyword list.
    result = _keyword_classify("t_thing_ambientocclusion")
    assert result is not None
    assert result[1] == "ambientocclusion"


def test_keyword_classify_albedo_routes_to_base_color():
    """Obduction-style ``_A`` suffix is too ambiguous — but ``albedo`` is clear."""
    result = _keyword_classify("t_battery_albedo")
    assert result is not None
    assert result[0] == "base_color"
    assert result[1] == "albedo"


# ---------------------------------------------------------------------------
# _scan_folder_for_psk — path-walk heuristic
# ---------------------------------------------------------------------------

def test_scan_folder_prefers_textures_dir(tmp_path):
    """Walking up from a PSK should find a 'Textures' ancestor."""
    textures = tmp_path / "Game" / "Battery" / "Textures"
    psk_dir = textures / "Mesh"
    psk_dir.mkdir(parents=True)
    psk = psk_dir / "BatteryA.pskx"
    chosen = _scan_folder_for_psk(psk)
    assert chosen == str(textures)


def test_scan_folder_falls_back_to_psk_parent(tmp_path):
    """When no Textures/Materials ancestor exists, fall back to PSK's parent."""
    psk_dir = tmp_path / "Some" / "Random" / "Folder"
    psk_dir.mkdir(parents=True)
    psk = psk_dir / "thing.pskx"
    chosen = _scan_folder_for_psk(psk)
    assert chosen == str(psk_dir)


# ---------------------------------------------------------------------------
# resolve_textures — end-to-end keyword fallback behavior
# ---------------------------------------------------------------------------

@pytest.fixture
def fallback_presets():
    """Minimal presets where suffix matching is intentionally restricted."""
    return {
        "version": 1,
        "ignore_textures": [],
        "ignore_patterns": [],
        "presets": {
            "default_pbr": {
                "description": "Test",
                "enable_keyword_fallback": True,
                "priority_order": ["base_color", "normal"],
                "texture_slots": {
                    "base_color": {
                        "suffixes": ["_C"],  # won't match Obduction's _A names
                        "param_names": [],
                        "colorspace": "sRGB",
                        "wiring": {"type": "direct", "target_input": "Base Color"},
                    },
                    "normal": {
                        "suffixes": ["_N"],
                        "param_names": [],
                        "colorspace": "Non-Color",
                        "wiring": {"type": "normal_map"},
                    },
                },
            }
        },
        "material_overrides": {},
    }


def test_keyword_fallback_fills_empty_slots(make_fake_sdk, fallback_presets, tmp_path):
    """When suffix matching produces no textures, fallback finds them by keyword."""
    folder = tmp_path / "Battery" / "Textures"
    folder.mkdir(parents=True)
    diffuse = folder / "T_Battery_Diffuse.tga"
    normal = folder / "T_Battery_Normal.tga"
    diffuse.write_bytes(b"\0")
    normal.write_bytes(b"\0")

    psk = tmp_path / "Battery" / "Mesh" / "BatteryA.pskx"
    psk.parent.mkdir(parents=True)
    psk.write_bytes(b"\0")

    # The folder walker would land at <tmp>/Battery/Textures because that's
    # the nearest "Textures" ancestor of the PSK.
    sdk = make_fake_sdk(
        textures={},
        folder_textures={str(folder): [diffuse, normal]},
    )

    res = resolve_textures(
        texture_names=["T_Battery_Custom_A", "T_Battery_Custom_M"],
        presets_data=fallback_presets,
        sdk=sdk,
        material_name="BatteryMetals_A",
        reference_path=psk,
    )

    slots_filled = {t.slot for t in res.resolved}
    assert "base_color" in slots_filled
    assert "normal" in slots_filled
    assert "base_color" in res.keyword_fallback_used
    assert "normal" in res.keyword_fallback_used


def test_keyword_fallback_skipped_when_disabled(make_fake_sdk, fallback_presets, tmp_path):
    """Setting _auto_resolve_fallback=False on presets_data turns it off."""
    folder = tmp_path / "Battery" / "Textures"
    folder.mkdir(parents=True)
    diffuse = folder / "T_Battery_Diffuse.tga"
    diffuse.write_bytes(b"\0")

    psk = tmp_path / "Battery" / "Mesh" / "BatteryA.pskx"
    psk.parent.mkdir(parents=True)

    sdk = make_fake_sdk(folder_textures={str(folder): [diffuse]})

    presets = dict(fallback_presets)
    presets["_auto_resolve_fallback"] = False

    res = resolve_textures(
        texture_names=["T_Battery_Custom_A"],
        presets_data=presets,
        sdk=sdk,
        material_name="X",
        reference_path=psk,
    )
    assert res.resolved == []
    assert res.keyword_fallback_used == []


def test_keyword_fallback_only_fills_empty_slots(make_fake_sdk, fallback_presets, tmp_path):
    """A confident suffix match must NOT be overwritten by the fallback."""
    folder = tmp_path / "Battery" / "Textures"
    folder.mkdir(parents=True)
    # Both a fallback-keyword and a direct-suffix-match texture exist on disk.
    keyword_diffuse = folder / "T_Battery_Diffuse.tga"
    keyword_diffuse.write_bytes(b"\0")
    suffix_match = folder / "T_BatterySpecific_C.tga"
    suffix_match.write_bytes(b"\0")

    psk = tmp_path / "Battery" / "Mesh" / "BatteryA.pskx"
    psk.parent.mkdir(parents=True)

    sdk = make_fake_sdk(
        textures={"t_batteryspecific_c": [suffix_match]},
        folder_textures={str(folder): [keyword_diffuse, suffix_match]},
    )

    res = resolve_textures(
        texture_names=["T_BatterySpecific_C"],
        presets_data=fallback_presets,
        sdk=sdk,
        material_name="X",
        reference_path=psk,
    )

    base_colors = [t for t in res.resolved if t.slot == "base_color"]
    assert len(base_colors) == 1
    # Suffix match wins — the keyword scan must not have stomped it.
    assert base_colors[0].path == suffix_match
    assert "base_color" not in res.keyword_fallback_used


def test_keyword_fallback_no_match_leaves_unresolved(
    make_fake_sdk, fallback_presets, tmp_path
):
    """If the folder has no recognizable textures, slots stay empty."""
    folder = tmp_path / "Battery" / "Textures"
    folder.mkdir(parents=True)
    junk = folder / "T_Random_Garbage.tga"
    junk.write_bytes(b"\0")

    psk = tmp_path / "Battery" / "Mesh" / "BatteryA.pskx"
    psk.parent.mkdir(parents=True)

    sdk = make_fake_sdk(folder_textures={str(folder): [junk]})

    res = resolve_textures(
        texture_names=["T_Battery_Custom_A"],
        presets_data=fallback_presets,
        sdk=sdk,
        material_name="X",
        reference_path=psk,
    )
    assert res.resolved == []
    assert res.keyword_fallback_used == []


def test_keyword_fallback_skipped_when_no_reference_path(
    make_fake_sdk, fallback_presets
):
    """Without a reference_path we can't pick a folder — skip the fallback."""
    sdk = make_fake_sdk(folder_textures={})
    res = resolve_textures(
        texture_names=["T_Battery_Custom_A"],
        presets_data=fallback_presets,
        sdk=sdk,
        material_name="X",
        reference_path=None,
    )
    assert res.keyword_fallback_used == []
