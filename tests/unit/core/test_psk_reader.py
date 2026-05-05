"""Unit tests for `core.psk_reader.read_psk`."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from core.psk_reader import PskParseError, read_psk

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Synthetic PSK builders
# ---------------------------------------------------------------------------

def _chunk_header(chunk_id: str, type_flag: int, data_size: int, data_count: int) -> bytes:
    return chunk_id.encode("ascii").ljust(20, b"\x00") + struct.pack(
        "<III", type_flag, data_size, data_count
    )


def _build_minimal_psk(
    points: list[tuple[float, float, float]],
    wedges: list[tuple[int, float, float]],   # (point_idx, u, v)
    faces: list[tuple[int, int, int]],         # wedge indices
    materials: list[str] | None = None,
    *,
    wedge_size: int = 16,
    face_wide: bool = False,
) -> bytes:
    out = bytearray()
    out += _chunk_header("ACTRHEAD", 0, 0, 0)

    out += _chunk_header("PNTS0000", 0, 12, len(points))
    for x, y, z in points:
        out += struct.pack("<fff", x, y, z)

    out += _chunk_header("VTXW0000", 0, wedge_size, len(wedges))
    for pi, u, v in wedges:
        if wedge_size == 16:
            out += struct.pack("<I", pi) + struct.pack("<ff", u, v) + b"\x00" * 4
        elif wedge_size == 12:
            out += struct.pack("<H", pi) + b"\x00" * 2 + struct.pack("<ff", u, v)
        else:
            raise ValueError(f"unsupported wedge_size {wedge_size}")

    if face_wide:
        face_size = 18
        out += _chunk_header("FACE3200", 0, face_size, len(faces))
        for a, b_, c in faces:
            out += struct.pack("<III", a, b_, c) + b"\x00" * 6
    else:
        face_size = 12
        out += _chunk_header("FACE0000", 0, face_size, len(faces))
        for a, b_, c in faces:
            out += struct.pack("<HHH", a, b_, c) + b"\x00" * 6

    if materials is not None:
        mat_size = 88
        out += _chunk_header("MATT0000", 0, mat_size, len(materials))
        for name in materials:
            out += name.encode("utf-8").ljust(64, b"\x00") + b"\x00" * (mat_size - 64)

    return bytes(out)


# ---------------------------------------------------------------------------
# Cube fixture (used for bounds + normals checks)
# ---------------------------------------------------------------------------

def _cube_psk_bytes(*, wedge_size: int = 16, face_wide: bool = False) -> bytes:
    pts = [
        (-1, -1, -1), ( 1, -1, -1), ( 1,  1, -1), (-1,  1, -1),
        (-1, -1,  1), ( 1, -1,  1), ( 1,  1,  1), (-1,  1,  1),
    ]
    # 12 triangles, one wedge per face corner = 36 wedges, simple uvs.
    quads = [
        (0, 1, 2, 3), (5, 4, 7, 6), (4, 0, 3, 7),
        (1, 5, 6, 2), (3, 2, 6, 7), (4, 5, 1, 0),
    ]
    wedges: list[tuple[int, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for q in quads:
        a, b_, c, d = q
        base = len(wedges)
        wedges.append((a, 0.0, 0.0))
        wedges.append((b_, 1.0, 0.0))
        wedges.append((c, 1.0, 1.0))
        wedges.append((d, 0.0, 1.0))
        faces.append((base + 0, base + 1, base + 2))
        faces.append((base + 0, base + 2, base + 3))
    return _build_minimal_psk(
        pts, wedges, faces, ["M_Cube"],
        wedge_size=wedge_size, face_wide=face_wide,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_read_psk_basic_triangle(tmp_path: Path):
    pts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    wedges = [(0, 0.0, 0.0), (1, 1.0, 0.0), (2, 0.0, 1.0)]
    faces = [(0, 1, 2)]
    p = tmp_path / "tri.psk"
    p.write_bytes(_build_minimal_psk(pts, wedges, faces, ["M_Tri"]))
    mesh = read_psk(p)

    assert mesh.vert_count == 3
    assert mesh.tri_count == 1
    assert mesh.material_names == ["M_Tri"]
    np.testing.assert_allclose(mesh.verts[0], [0, 0, 0])
    np.testing.assert_allclose(mesh.verts[1], [1, 0, 0])
    np.testing.assert_allclose(mesh.verts[2], [0, 1, 0])
    np.testing.assert_allclose(mesh.uv0[2], [0.0, 1.0])


def test_read_psk_supports_12_byte_wedges(tmp_path: Path):
    pts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    wedges = [(0, 0.0, 0.0), (1, 1.0, 0.0), (2, 0.0, 1.0)]
    faces = [(0, 1, 2)]
    p = tmp_path / "tri12.psk"
    p.write_bytes(_build_minimal_psk(pts, wedges, faces, wedge_size=12))
    mesh = read_psk(p)
    assert mesh.tri_count == 1


def test_read_psk_supports_face3200(tmp_path: Path):
    pts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    wedges = [(0, 0.0, 0.0), (1, 1.0, 0.0), (2, 0.0, 1.0)]
    faces = [(0, 1, 2)]
    p = tmp_path / "tri_wide.psk"
    p.write_bytes(_build_minimal_psk(pts, wedges, faces, face_wide=True))
    mesh = read_psk(p)
    assert mesh.tri_count == 1


def test_read_psk_cube_bounds_and_normals(tmp_path: Path):
    p = tmp_path / "cube.psk"
    p.write_bytes(_cube_psk_bytes())
    mesh = read_psk(p)

    np.testing.assert_allclose(mesh.bounds_min, [-1, -1, -1])
    np.testing.assert_allclose(mesh.bounds_max, [ 1,  1,  1])
    np.testing.assert_allclose(mesh.center, [0, 0, 0])
    # |bmax - center| = sqrt(3)
    assert mesh.radius == pytest.approx(np.sqrt(3.0), rel=1e-5)
    # Each wedge normal must be unit length.
    lens = np.linalg.norm(mesh.normals, axis=1)
    np.testing.assert_allclose(lens, np.ones_like(lens), atol=1e-5)


def test_read_psk_no_matt_returns_empty_material_list(tmp_path: Path):
    pts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    wedges = [(0, 0.0, 0.0), (1, 1.0, 0.0), (2, 0.0, 1.0)]
    faces = [(0, 1, 2)]
    p = tmp_path / "no_matt.psk"
    p.write_bytes(_build_minimal_psk(pts, wedges, faces, materials=None))
    mesh = read_psk(p)
    assert mesh.material_names == []


def test_read_psk_truncated_mid_chunk_raises(tmp_path: Path):
    p = tmp_path / "truncated.psk"
    p.write_bytes(b"\x00" * 10)  # less than a single 32-byte header
    with pytest.raises(PskParseError):
        read_psk(p)


def test_read_psk_chunk_overrun_raises(tmp_path: Path):
    bogus = _chunk_header("PNTS0000", 0, 100_000, 100_000)
    p = tmp_path / "overrun.psk"
    p.write_bytes(bogus)  # 32 bytes total claiming 10B payload
    with pytest.raises(PskParseError):
        read_psk(p)


def test_read_psk_face_index_out_of_range_raises(tmp_path: Path):
    pts = [(0.0, 0.0, 0.0)]
    wedges = [(0, 0.0, 0.0)]
    faces = [(0, 1, 2)]   # references wedges 1 and 2 which don't exist
    p = tmp_path / "bad_face.psk"
    p.write_bytes(_build_minimal_psk(pts, wedges, faces))
    with pytest.raises(PskParseError):
        read_psk(p)


def test_read_psk_all_empty_returns_zero_mesh(tmp_path: Path):
    """A PSK with no chunks at all is treated as an empty mesh, not an error."""
    p = tmp_path / "empty.psk"
    p.write_bytes(b"")
    mesh = read_psk(p)
    assert mesh.vert_count == 0
    assert mesh.tri_count == 0


def test_read_psk_existing_fixture(fixtures_dir: Path):
    """The pre-existing minimal.psk fixture parses material names cleanly."""
    psk = fixtures_dir / "psk" / "minimal.psk"
    if not psk.is_file():
        pytest.skip("minimal.psk fixture not present")
    mesh = read_psk(psk)
    assert mesh.material_names == ["M_TestBody", "M_TestHelmet"]
