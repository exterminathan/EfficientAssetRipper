"""Unit tests for `core.asset_scanner._extract_psk_materials` (binary chunk parser).

`_extract_psk_materials` returns ``(names, ok)`` where ``ok=False`` signals a
malformed file — distinct from a clean file that simply has no MATT chunk.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from core.asset_scanner import _extract_psk_materials

pytestmark = pytest.mark.unit


def test_extract_psk_materials_finds_matt_chunk(fixtures_dir: Path):
    psk = fixtures_dir / "psk" / "minimal.psk"
    names, ok = _extract_psk_materials(psk)
    assert ok is True
    assert names == ["M_TestBody", "M_TestHelmet"]


def test_extract_psk_materials_returns_empty_when_no_matt(fixtures_dir: Path):
    """A well-formed PSK with no MATT chunk returns ([], True)."""
    psk = fixtures_dir / "psk" / "no_matt.psk"
    names, ok = _extract_psk_materials(psk)
    assert names == []
    assert ok is True


def test_extract_psk_materials_handles_unreadable_file(tmp_path: Path):
    """A nonexistent path returns ([], False) — file unreadable is malformed."""
    missing = tmp_path / "does_not_exist.psk"
    names, ok = _extract_psk_materials(missing)
    assert names == []
    assert ok is False


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
    names, ok = _extract_psk_materials(p)
    assert ok is True
    assert names == ["M_Padded"]


def test_extract_psk_materials_skips_unreadable_chunk_id(tmp_path: Path):
    """Chunk IDs containing non-ASCII bytes should mark the file as malformed."""
    bogus_id = bytes([0xFF, 0xFE, 0xFD]) + b"\x00" * 17
    bogus_chunk = bogus_id + struct.pack("<III", 0, 0, 0)
    p = tmp_path / "bogus.psk"
    p.write_bytes(bogus_chunk)
    names, ok = _extract_psk_materials(p)
    assert names == []
    assert ok is False


def test_extract_psk_materials_rejects_chunk_overrun(tmp_path: Path):
    """A chunk header claiming more payload than the file holds is malformed."""
    bogus = b"ACTRHEAD".ljust(20, b"\x00") + struct.pack(
        "<III", 0, 100_000, 100_000
    )
    p = tmp_path / "overrun.psk"
    p.write_bytes(bogus)  # Only 32 bytes total, claims 100k * 100k payload
    names, ok = _extract_psk_materials(p)
    assert names == []
    assert ok is False


def test_extract_psk_materials_rejects_implausibly_huge_chunk(tmp_path: Path):
    """Chunk dims beyond _MAX_PSK_CHUNK_BYTES must not be trusted."""
    # 200 MB > _MAX_PSK_CHUNK_BYTES (100 MB)
    payload_dsize = 200 * 1024 * 1024
    bogus = b"FACE0000".ljust(20, b"\x00") + struct.pack(
        "<III", 0, payload_dsize, 1
    )
    p = tmp_path / "huge.psk"
    p.write_bytes(bogus)
    names, ok = _extract_psk_materials(p)
    assert names == []
    assert ok is False


def test_extract_psk_materials_truncated_mid_header(tmp_path: Path):
    """A file with fewer bytes than a single chunk header is malformed."""
    p = tmp_path / "tiny.psk"
    p.write_bytes(b"\x00" * 10)
    names, ok = _extract_psk_materials(p)
    assert names == []
    assert ok is False


def test_extract_psk_materials_decodes_utf8_material_name(tmp_path: Path):
    """Material names with UTF-8 bytes (Cyrillic/CJK) decode cleanly."""
    cyrillic = "M_Привет".encode("utf-8")
    raw = cyrillic.ljust(64, b"\x00")
    mat_data = raw + struct.pack("<IIIIII", 0, 0, 0, 0, 0, 0)
    matt_header = b"MATT0000".ljust(20, b"\x00") + struct.pack(
        "<III", 0, len(mat_data), 1
    )
    p = tmp_path / "cyrillic.psk"
    p.write_bytes(matt_header + mat_data)
    names, ok = _extract_psk_materials(p)
    assert ok is True
    assert names == ["M_Привет"]


def test_extract_psk_materials_falls_back_to_cp1252(tmp_path: Path):
    """Bytes that aren't valid UTF-8 fall back through cp1252."""
    # 0x91, 0x92 are cp1252 left/right single quote — not valid UTF-8.
    cp1252_name = b"M_Foo" + bytes([0x91, 0x92])
    raw = cp1252_name.ljust(64, b"\x00")
    mat_data = raw + struct.pack("<IIIIII", 0, 0, 0, 0, 0, 0)
    matt_header = b"MATT0000".ljust(20, b"\x00") + struct.pack(
        "<III", 0, len(mat_data), 1
    )
    p = tmp_path / "cp1252.psk"
    p.write_bytes(matt_header + mat_data)
    names, ok = _extract_psk_materials(p)
    assert ok is True
    assert names == ["M_Foo‘’"]
