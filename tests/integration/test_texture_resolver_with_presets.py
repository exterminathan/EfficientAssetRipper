"""Integration tests for `core.texture_resolver.resolve_textures` with FakeEverythingSDK."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.texture_resolver import resolve_textures
from tests.conftest import FakeEverythingSDK

pytestmark = pytest.mark.integration


@pytest.fixture
def presets():
    return {
        "version": 1,
        "ignore_textures": ["DefaultTexture"],
        "ignore_patterns": ["BlendFunc"],
        "presets": {
            "default_pbr": {
                "texture_slots": {
                    "base_color": {
                        "suffixes": ["_C", "_BaseColor"],
                        "param_names": ["BaseColor"],
                        "colorspace": "sRGB",
                        "wiring": {"type": "direct", "target_input": "Base Color"},
                    },
                    "normal": {
                        "suffixes": ["_N"],
                        "param_names": ["Normal"],
                        "colorspace": "Non-Color",
                        "wiring": {"type": "normal_map"},
                    },
                    "orm": {
                        "suffixes": ["_ORM"],
                        "param_names": [],
                        "colorspace": "Non-Color",
                        "wiring": {"type": "split_channels"},
                    },
                },
            }
        },
        "material_overrides": {
            "MI_Override_Demo": {
                "preset": "default_pbr",
                "force_textures": {"base_color": "ForcedBaseColor"},
            }
        },
    }


def test_resolve_textures_default_pbr_with_fake_sdk(presets):
    sdk = FakeEverythingSDK(textures={
        "T_Body_C": [Path(r"C:\Game\Textures\T_Body_C.tga")],
        "T_Body_N": [Path(r"C:\Game\Textures\T_Body_N.tga")],
    })
    res = resolve_textures(
        texture_names=["T_Body_C", "T_Body_N"],
        presets_data=presets,
        sdk=sdk,
    )
    slots = {r.slot for r in res.resolved}
    assert slots == {"base_color", "normal"}
    assert not res.unresolved


def test_resolve_textures_material_override_force_textures(presets):
    sdk = FakeEverythingSDK(textures={
        "ForcedBaseColor": [Path(r"C:\Game\Forced.tga")],
        "T_OtherC": [Path(r"C:\Game\T_OtherC.tga")],
    })
    res = resolve_textures(
        texture_names=["T_OtherC"],     # Would normally fill base_color
        presets_data=presets,
        sdk=sdk,
        material_name="MI_Override_Demo",
    )
    base = next(r for r in res.resolved if r.slot == "base_color")
    assert base.texture_name == "ForcedBaseColor"
    assert base.path == Path(r"C:\Game\Forced.tga")


def test_resolve_textures_global_ignore_textures_skipped(presets):
    sdk = FakeEverythingSDK(textures={
        "T_Body_C": [Path(r"C:\Game\T_Body_C.tga")],
    })
    res = resolve_textures(
        texture_names=["DefaultTexture", "T_Body_C"],
        presets_data=presets,
        sdk=sdk,
    )
    # DefaultTexture is in ignore_textures — skipped silently (not unresolved)
    names_resolved = {r.texture_name for r in res.resolved}
    names_unres = {u.texture_name for u in res.unresolved}
    assert "DefaultTexture" not in names_resolved
    assert "DefaultTexture" not in names_unres


def test_resolve_textures_ignore_pattern_substring_matches(presets):
    sdk = FakeEverythingSDK()
    res = resolve_textures(
        texture_names=["BlendFunc_Mask_Z"],     # contains substring "BlendFunc"
        presets_data=presets,
        sdk=sdk,
    )
    assert not res.resolved
    assert not res.unresolved   # Skipped via ignore_pattern


def test_resolve_textures_unknown_preset_marks_all_unresolved(presets):
    sdk = FakeEverythingSDK(textures={"T_X_C": [Path(r"C:\X.tga")]})
    res = resolve_textures(
        texture_names=["T_X_C"],
        presets_data=presets,
        sdk=sdk,
        preset_name="nonexistent_preset",
    )
    assert not res.resolved
    assert len(res.unresolved) == 1
    assert "preset" in res.unresolved[0].reason.lower()


def test_resolve_textures_picks_closest_path_when_multiple_candidates(presets):
    near = Path(r"C:\Game\Char\Trooper\Textures\T_Body_C.tga")
    far = Path(r"C:\Game\Char\Other\Textures\T_Body_C.tga")
    sdk = FakeEverythingSDK(textures={"T_Body_C": [far, near]})
    res = resolve_textures(
        texture_names=["T_Body_C"],
        presets_data=presets,
        sdk=sdk,
        reference_path=Path(r"C:\Game\Char\Trooper\Meshes\SK_Trooper.psk"),
    )
    assert len(res.resolved) == 1
    assert res.resolved[0].path == near


def test_resolve_textures_file_not_found_marked_unresolved(presets):
    sdk = FakeEverythingSDK(textures={})
    res = resolve_textures(
        texture_names=["T_NotOnDisk_C"],
        presets_data=presets,
        sdk=sdk,
    )
    assert not res.resolved
    assert len(res.unresolved) == 1
    assert res.unresolved[0].reason == "file_not_found"


def test_resolve_textures_no_matching_suffix_marked_unresolved(presets):
    sdk = FakeEverythingSDK(textures={})
    res = resolve_textures(
        texture_names=["T_Garbage_Name_With_No_Suffix"],
        presets_data=presets,
        sdk=sdk,
    )
    assert len(res.unresolved) == 1
    assert res.unresolved[0].reason == "no_matching_suffix"
