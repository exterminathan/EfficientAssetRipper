"""Unit tests for AssetEntry serialization (cache round-trip + manifest export)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.asset_scanner import (
    AssetEntry,
    MaterialEntry,
    _asset_to_dict,
    _dict_to_asset,
    _CACHE_VERSION,
    save_scan_cache,
    load_scan_cache,
)
from core.texture_resolver import ResolvedTexture, UnresolvedTexture

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def _sample_entry() -> AssetEntry:
    tex = ResolvedTexture(
        slot="base_color",
        texture_name="T_Trooper_Body_C",
        path=Path(r"C:\Game\T_Trooper_Body_C.tga"),
        colorspace="sRGB",
        wiring={"type": "direct", "target_input": "Base Color"},
    )
    unres = UnresolvedTexture(texture_name="T_Missing", reason="no_matching_suffix")
    mat = MaterialEntry(
        slot_name="BodySlot",
        material_name="MI_Trooper_Body",
        textures=[tex],
        unresolved=[unres],
        props_found=True,
        preset_used="default_pbr",
        bsdf_overrides={"Roughness": 0.5},
        color_tints={"RColorTint": (0.6, 0.4, 0.1, 1.0)},
        scalar_params={"Tweak": 0.42},
        parent_name="MM_Master",
    )
    return AssetEntry(
        psk_path=Path(r"C:\Game\Char\SK_Trooper.psk"),
        name="SK_Trooper",
        materials=[mat],
        mesh_props_found=True,
        total_textures=2,
        missing_textures=1,
        category="Characters",
        subcategory="Trooper",
    )


def test_dict_roundtrip_preserves_psk_path():
    e = _sample_entry()
    e2 = _dict_to_asset(_asset_to_dict(e))
    assert e2.psk_path == e.psk_path
    assert e2.name == e.name


def test_dict_roundtrip_preserves_resolved_textures():
    e = _sample_entry()
    e2 = _dict_to_asset(_asset_to_dict(e))
    assert len(e2.materials) == 1
    tex_round = e2.materials[0].textures[0]
    tex_orig = e.materials[0].textures[0]
    assert tex_round.slot == tex_orig.slot
    assert tex_round.path == tex_orig.path
    assert tex_round.colorspace == tex_orig.colorspace
    assert tex_round.wiring == tex_orig.wiring


def test_dict_roundtrip_preserves_unresolved_reasons():
    e = _sample_entry()
    e2 = _dict_to_asset(_asset_to_dict(e))
    unres_round = e2.materials[0].unresolved[0]
    assert unres_round.texture_name == "T_Missing"
    assert unres_round.reason == "no_matching_suffix"


def test_dict_roundtrip_preserves_color_tints_and_scalar_params():
    e = _sample_entry()
    e2 = _dict_to_asset(_asset_to_dict(e))
    # JSON serialization will turn tuples into lists — accept either
    rc = e2.materials[0].color_tints["RColorTint"]
    assert tuple(rc) == (0.6, 0.4, 0.1, 1.0)
    assert e2.materials[0].scalar_params["Tweak"] == pytest.approx(0.42)
    assert e2.materials[0].parent_name == "MM_Master"


# ---------------------------------------------------------------------------
# Manifest output (consumed by the Blender script)
# ---------------------------------------------------------------------------

def test_to_manifest_shape():
    e = _sample_entry()
    out = e.to_manifest(Path(r"C:\Out\SK_Trooper.blend"), "bl_ext.test.addon")
    assert set(out.keys()) >= {"psk_path", "output_path", "addon_name", "materials"}
    assert out["addon_name"] == "bl_ext.test.addon"
    assert out["psk_path"] == str(e.psk_path)
    body = out["materials"]["BodySlot"]
    assert body["material_name"] == "MI_Trooper_Body"
    # The texture must include path, colorspace, wiring
    bc = body["textures"]["base_color"]
    assert bc["colorspace"] == "sRGB"
    assert bc["wiring"]["type"] == "direct"


def test_to_manifest_handles_empty_materials():
    e = AssetEntry(psk_path=Path("foo.psk"), name="foo")
    out = e.to_manifest(Path("foo.blend"), "addon.x")
    assert out["materials"] == {}


# ---------------------------------------------------------------------------
# Status / status_text properties
# ---------------------------------------------------------------------------

def test_status_no_props_when_mesh_props_missing():
    e = AssetEntry(psk_path=Path("x.psk"), name="x", mesh_props_found=False)
    assert e.status == "no_props"
    assert e.status_text == "No .props.txt found"


def test_status_no_materials_when_empty_list():
    e = AssetEntry(psk_path=Path("x.psk"), name="x", materials=[])
    assert e.status == "no_materials"


def test_status_ready_when_all_textures_resolved():
    e = _sample_entry()
    # Sample has 1 missing — adjust:
    e.missing_textures = 0
    e.total_textures = 1
    assert e.status == "ready"
    assert "Ready" in e.status_text


def test_status_processed_when_processed_flag_set():
    e = _sample_entry()
    e.processed = True
    assert e.status == "processed"
    assert e.status_text == "Processed"


# ---------------------------------------------------------------------------
# Cache file I/O
# ---------------------------------------------------------------------------

def test_save_scan_cache_writes_versioned_json(tmp_path, monkeypatch):
    """save_scan_cache writes a versioned JSON file at the hashed cache path."""
    # Redirect _DEFAULT_CACHE_DIR
    import core.asset_scanner as scanner_mod
    monkeypatch.setattr(scanner_mod, "_DEFAULT_CACHE_DIR", tmp_path / "cache")

    e = _sample_entry()
    cache_path = save_scan_cache([e], r"C:\Games\TestGame")
    assert cache_path.is_file()

    data = json.loads(cache_path.read_text())
    assert data["version"] == _CACHE_VERSION
    assert data["asset_count"] == 1
    assert data["game_folder"] == r"C:\Games\TestGame"
    assert data["assets"][0]["name"] == "SK_Trooper"


def test_load_scan_cache_returns_none_when_no_cache_for_folder(tmp_path, monkeypatch):
    import core.asset_scanner as scanner_mod
    monkeypatch.setattr(scanner_mod, "_DEFAULT_CACHE_DIR", tmp_path / "cache")
    assert load_scan_cache(r"C:\Games\Nope") is None


def test_save_then_load_roundtrip(tmp_path, monkeypatch):
    import core.asset_scanner as scanner_mod
    monkeypatch.setattr(scanner_mod, "_DEFAULT_CACHE_DIR", tmp_path / "cache")

    folder = r"C:\Games\Roundtrip"
    e = _sample_entry()
    save_scan_cache([e], folder)
    loaded = load_scan_cache(folder)
    assert loaded is not None
    entries, ts = loaded
    assert len(entries) == 1
    assert entries[0].name == "SK_Trooper"
    assert entries[0].materials[0].material_name == "MI_Trooper_Body"


# ---------------------------------------------------------------------------
# Cache version mismatch — rename + clean rebuild (Phase 2.1)
# ---------------------------------------------------------------------------

def test_load_scan_cache_renames_old_version_and_returns_none(tmp_path, monkeypatch):
    """When the cached version doesn't match _CACHE_VERSION, the file gets
    renamed to *.json.bak.<ts> and load returns None (forces a re-scan)."""
    import core.asset_scanner as scanner_mod
    monkeypatch.setattr(scanner_mod, "_DEFAULT_CACHE_DIR", tmp_path / "cache")
    folder = r"C:\Games\Old"

    # Save with the current version, then rewrite with a stale version.
    e = _sample_entry()
    cache_path = save_scan_cache([e], folder)
    raw = json.loads(cache_path.read_text())
    raw["version"] = _CACHE_VERSION + 99   # pretend an older schema
    cache_path.write_text(json.dumps(raw))

    result = load_scan_cache(folder)
    assert result is None
    # The original cache file should have been renamed.
    assert not cache_path.is_file()
    backups = list((tmp_path / "cache").glob("scan_*.json.bak.*"))
    assert len(backups) == 1
    # The backup's payload has the bumped version, intact.
    assert json.loads(backups[0].read_text())["version"] == _CACHE_VERSION + 99


def test_sweep_old_cache_backups_removes_stale(tmp_path, monkeypatch):
    """Backups older than retention_days are deleted."""
    import time
    from core.asset_scanner import sweep_old_cache_backups
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    fresh = cache_dir / "scan_aaaa.json.bak.99999999"
    stale = cache_dir / "scan_bbbb.json.bak.11111111"
    fresh.write_text("{}")
    stale.write_text("{}")
    # Backdate the stale file by 60 days.
    old_ts = time.time() - (60 * 86400)
    import os
    os.utime(stale, (old_ts, old_ts))

    deleted = sweep_old_cache_backups(cache_dir, retention_days=30)
    assert deleted == 1
    assert fresh.is_file()
    assert not stale.is_file()


# ---------------------------------------------------------------------------
# Cache: blend file deleted out-of-band flips processed back to False
# ---------------------------------------------------------------------------

def test_load_cache_flips_processed_off_when_blend_missing(tmp_path, monkeypatch):
    """If processed=True was cached but the .blend has been deleted, the
    auto-detect should flip it back to False so the asset becomes re-queueable.
    """
    import core.asset_scanner as scanner_mod
    monkeypatch.setattr(scanner_mod, "_DEFAULT_CACHE_DIR", tmp_path / "cache")

    blend_path = tmp_path / "deleted.blend"  # never created
    e = _sample_entry()
    e.blend_path = blend_path
    e.processed = True
    save_scan_cache([e], "TestGame")

    loaded = load_scan_cache("TestGame")
    assert loaded is not None
    entries, _ = loaded
    assert entries[0].processed is False
