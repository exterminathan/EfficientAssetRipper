"""Unit tests for `core.asset_scanner._extract_psk_materials` (binary chunk parser)."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from core.asset_scanner import _extract_psk_materials

pytestmark = pytest.mark.unit


def test_extract_psk_materials_finds_matt_chunk(fixtures_dir: Path):
    psk = fixtures_dir / "psk" / "minimal.psk"
    names = _extract_psk_materials(psk)
    assert names == ["M_TestBody", "M_TestHelmet"]


def test_extract_psk_materials_returns_empty_when_no_matt(fixtures_dir: Path):
    psk = fixtures_dir / "psk" / "no_matt.psk"
    assert _extract_psk_materials(psk) == []


def test_extract_psk_materials_handles_unreadable_file(tmp_path: Path):
    """A nonexistent path should return [] without raising."""
    missing = tmp_path / "does_not_exist.psk"
    assert _extract_psk_materials(missing) == []


def test_extract_psk_materials_truncates_on_null_bytes(tmp_path: Path):
    """Material names are NUL-terminated within their 64-byte slot."""
    mat_struct = struct.Struct("<64s I I I I I I")
    matA = mat_struct.pack(b"M_Padded\x00\x00\x00\x00garbage", 0, 0, 0, 0, 0, 0)

    header = b"ACTRHEAD".ljust(20, b"\x00") + struct.pack("<III", 0, 0, 0)
    matt_header = b"MATT0000".ljust(20, b"\x00") + struct.pack(
        "<III", 0, mat_struct.size, 1
    )

    p = tmp_path / "padded.psk"
    p.write_bytes(header + matt_header + matA)
    names = _extract_psk_materials(p)
    assert names == ["M_Padded"]


def test_extract_psk_materials_skips_unreadable_chunk_id(tmp_path: Path):
    """Chunk IDs containing non-ASCII bytes should bail out cleanly."""
    bogus_id = bytes([0xFF, 0xFE, 0xFD]) + b"\x00" * 17
    bogus_chunk = bogus_id + struct.pack("<III", 0, 0, 0)
    p = tmp_path / "bogus.psk"
    p.write_bytes(bogus_chunk)
    assert _extract_psk_materials(p) == []
