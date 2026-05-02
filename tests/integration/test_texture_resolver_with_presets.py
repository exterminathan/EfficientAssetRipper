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


# ---------------------------------------------------------------------------
# Phase 2.5: list-of-param-names — duplicate bindings shouldn't be lost
# ---------------------------------------------------------------------------

def test_resolve_textures_param_name_list_falls_back_per_binding(presets):
    """A textureless suffix that's only identifiable by one of several
    parameter bindings must classify via the matching one."""
    sdk = FakeEverythingSDK(textures={
        "T_NoSuffix": [Path(r"C:\Game\T_NoSuffix.tga")],
    })
    res = resolve_textures(
        texture_names=["T_NoSuffix"],
        presets_data=presets,
        sdk=sdk,
        # The texture is bound to BOTH `RandomThing` (won't match) and
        # `BaseColor` (matches base_color slot). We must not drop the second.
        param_name_map={"T_NoSuffix": ["RandomThing", "BaseColor"]},
    )
    assert len(res.resolved) == 1
    assert res.resolved[0].slot == "base_color"


def test_resolve_textures_legacy_str_param_name_still_supported(presets):
    """The old `dict[str, str]` shape stays compatible (single binding)."""
    sdk = FakeEverythingSDK(textures={
        "T_NoSuffix": [Path(r"C:\Game\T_NoSuffix.tga")],
    })
    res = resolve_textures(
        texture_names=["T_NoSuffix"],
        presets_data=presets,
        sdk=sdk,
        param_name_map={"T_NoSuffix": "BaseColor"},
    )
    assert len(res.resolved) == 1
    assert res.resolved[0].slot == "base_color"


# ---------------------------------------------------------------------------
# Phase 1.1 (integration): priority_order from real preset shape
# ---------------------------------------------------------------------------

def test_resolve_textures_uses_priority_order_for_cross_slot_ties():
    """priority_order from preset JSON should propagate into classification."""
    presets = {
        "version": 1,
        "presets": {
            "default_pbr": {
                "priority_order": ["base_color", "alpha"],
                "texture_slots": {
                    "alpha": {
                        "suffixes": ["_A"],
                        "param_names": [],
                        "colorspace": "Non-Color",
                        "wiring": {"type": "direct", "target_input": "Alpha"},
                    },
                    "base_color": {
                        "suffixes": ["_A"],   # same length as alpha
                        "param_names": [],
                        "colorspace": "sRGB",
                        "wiring": {"type": "direct", "target_input": "Base Color"},
                    },
                },
            }
        },
    }
    sdk = FakeEverythingSDK(textures={"T_Foo_A": [Path(r"C:\Game\T_Foo_A.tga")]})
    res = resolve_textures(
        texture_names=["T_Foo_A"],
        presets_data=presets,
        sdk=sdk,
    )
    assert len(res.resolved) == 1
    # priority_order placed base_color before alpha → it wins the tie.
    assert res.resolved[0].slot == "base_color"
