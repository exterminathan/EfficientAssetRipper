"""Integration tests against the real Jedi Survivor scan cache fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.asset_scanner import _dict_to_asset

pytestmark = pytest.mark.integration


def test_jedi_scan_loads_all_entries(jedi_scan_dict):
    assets = [_dict_to_asset(d) for d in jedi_scan_dict.get("assets", [])]
    assert len(assets) >= 1
    # Sanity: asset_count metadata matches
    declared = jedi_scan_dict.get("asset_count")
    if declared is not None:
        assert declared == len(assets)


def test_jedi_scan_yields_material_refs_per_asset(jedi_scan_dict):
    """Every material entry that exists must have a non-empty material_name."""
    assets = [_dict_to_asset(d) for d in jedi_scan_dict.get("assets", [])]
    bad: list[tuple[str, int]] = []
    for a in assets:
        for i, m in enumerate(a.materials):
            if not m.material_name:
                bad.append((a.name, i))
    assert not bad, f"materials missing material_name in {bad[:5]}"


def test_jedi_scan_psk_paths_have_known_extensions(jedi_scan_dict):
    assets = [_dict_to_asset(d) for d in jedi_scan_dict.get("assets", [])]
    bad: list[str] = []
    for a in assets:
        if a.psk_path.suffix.lower() not in {".psk", ".pskx"}:
            bad.append(str(a.psk_path))
    assert not bad, f"unexpected suffixes: {bad[:5]}"


def test_jedi_scan_status_text_for_every_entry(jedi_scan_dict):
    """`status_text` should never raise across all entries."""
    assets = [_dict_to_asset(d) for d in jedi_scan_dict.get("assets", [])]
    for a in assets:
        _ = a.status_text  # should not raise


def test_jedi_scan_total_textures_consistent(jedi_scan_dict):
    """missing_textures must be <= total_textures for every entry."""
    assets = [_dict_to_asset(d) for d in jedi_scan_dict.get("assets", [])]
    bad = [
        a.name for a in assets if a.missing_textures > a.total_textures
    ]
    assert not bad, f"missing > total for: {bad[:5]}"


def test_small_scan_loads(small_scan_dict):
    """Smoke check that the smaller scan also deserializes cleanly."""
    assets = [_dict_to_asset(d) for d in small_scan_dict.get("assets", [])]
    for a in assets:
        assert a.name
        assert a.psk_path
