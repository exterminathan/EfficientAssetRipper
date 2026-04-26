"""Tests for `config` module — defaults, get/set round-trip, presets loader."""

from __future__ import annotations

from pathlib import Path

import pytest

import config

pytestmark = pytest.mark.unit


REQUIRED_KEYS = {
    "game_folder",
    "blender_exe",
    "output_dir",
    "everything_dll",
    "psk_addon_name",
    "timeout_seconds",
    "presets_path",
    "cue4parse_cli",
    "unpack_output_dir",
    "aes_keys",
    "unpack_ue_version",
    "export_texture_format",
    "export_audio_format",
    "active_profile",
    "color_scheme",
    "custom_schemes",
}


def test_defaults_contain_required_keys():
    missing = REQUIRED_KEYS - set(config._DEFAULTS.keys())
    assert not missing, f"Missing default keys: {missing}"


def test_get_returns_default_when_unset(mock_qsettings):
    """An empty stub QSettings should fall back to the _DEFAULTS value."""
    assert config.get("psk_addon_name") == "bl_ext.blender_org.io_scene_psk_psa"
    assert config.get("export_texture_format") == "png"
    assert config.get("active_profile") == "Default"


def test_get_returns_empty_string_for_unknown_key(mock_qsettings):
    assert config.get("totally_unknown_key_nope") == ""


def test_set_then_get_persists_value(mock_qsettings):
    config.set("game_folder", r"C:\Games\Test")
    assert config.get("game_folder") == r"C:\Games\Test"


def test_get_int_returns_default_when_unset(mock_qsettings):
    assert config.get_int("timeout_seconds") == 120


def test_get_int_round_trip(mock_qsettings):
    config.set("timeout_seconds", 300)
    assert config.get_int("timeout_seconds") == 300


def test_addon_name_default_is_current_value():
    """Guards against accidental rewrite of the default addon name."""
    assert config._DEFAULTS["psk_addon_name"] == "bl_ext.blender_org.io_scene_psk_psa"


def test_get_presets_path_returns_path_object(mock_qsettings):
    p = config.get_presets_path()
    assert isinstance(p, Path)


def test_load_presets_reads_real_file(real_presets):
    """Verify the real data/texture_presets.json shape (the source of truth)."""
    assert "presets" in real_presets
    assert "default_pbr" in real_presets["presets"]
    default = real_presets["presets"]["default_pbr"]
    assert "texture_slots" in default
    # Must have at least the canonical slots
    slots = default["texture_slots"]
    for required in ("base_color",):
        assert required in slots, f"Missing required slot: {required}"


def test_all_keys_returns_list_with_required_keys():
    keys = config.all_keys()
    assert isinstance(keys, list)
    assert REQUIRED_KEYS <= set(keys)
