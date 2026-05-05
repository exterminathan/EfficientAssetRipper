"""Pure-Python PSK/PSKX reader for the in-app 3D previewer.

Reads only the chunks needed for visual preview — points, wedges, faces, UVs,
and material names. Skeleton, weights, morph targets, and extra UV layers are
deliberately skipped.

Returns a `PskMesh` with numpy arrays sized for direct GL upload (one entry
per wedge, indexed by triangle).
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np

log = logging.getLogger(__name__)


# Re-uses the same conventions as core.asset_scanner._extract_psk_materials.
_MAX_PSK_CHUNK_BYTES = 100 * 1024 * 1024
_HEADER_SIZE = 32


@dataclass
class ChunkHeader:
    chunk_id: str
    type_flag: int
    data_size: int
    data_count: int
    payload_offset: int  # absolute file offset where the payload starts

    @property
    def payload_bytes(self) -> int:
        return self.data_size * self.data_count


class PskParseError(ValueError):
    """Raised when a PSK/PSKX file is malformed or unreadable."""


@dataclass
class PskMesh:
    """Mesh geometry ready for GL upload.

    All arrays are wedge-indexed: one entry per face corner. ``faces`` indexes
    into ``verts`` / ``normals`` / ``uv0`` directly.
    """
    verts: np.ndarray       # (W, 3) float32 — wedge positions (point[wedge.point_idx])
    normals: np.ndarray     # (W, 3) float32 — averaged face normals per wedge
    uv0: np.ndarray         # (W, 2) float32
    faces: np.ndarray       # (T, 3) uint32 — triangle wedge indices
    material_names: list[str] = field(default_factory=list)
    bounds_min: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float32))
    bounds_max: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float32))
    center: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float32))
    radius: float = 1.0

    @property
    def vert_count(self) -> int:
        return int(self.verts.shape[0])

    @property
    def tri_count(self) -> int:
        return int(self.faces.shape[0])


# ---------------------------------------------------------------------------
# Chunk iteration
# ---------------------------------------------------------------------------

def _iter_chunks(data: bytes) -> Iterator[ChunkHeader]:
    """Yield chunk headers in order. Raises PskParseError on malformed files."""
    offset = 0
    size = len(data)
    while offset < size:
        if offset + _HEADER_SIZE > size:
            raise PskParseError(
                f"truncated mid-header at offset {offset}/{size}"
            )
        chunk_id_raw = data[offset:offset + 20].split(b"\x00")[0]
        try:
            chunk_id = chunk_id_raw.decode("ascii")
        except UnicodeDecodeError:
            raise PskParseError(
                f"non-ASCII chunk id at offset {offset}"
            )

        type_flag, dsize, dcount = struct.unpack_from("<III", data, offset + 20)

        if dsize > _MAX_PSK_CHUNK_BYTES or dcount > _MAX_PSK_CHUNK_BYTES:
            raise PskParseError(
                f"implausible chunk dims at {chunk_id}: dsize={dsize} dcount={dcount}"
            )
        chunk_bytes = dsize * dcount
        remaining = size - offset - _HEADER_SIZE
        if chunk_bytes > _MAX_PSK_CHUNK_BYTES or chunk_bytes > remaining:
            raise PskParseError(
                f"chunk {chunk_id} overruns end (need {chunk_bytes}, have {remaining})"
            )

        yield ChunkHeader(
            chunk_id=chunk_id,
            type_flag=type_flag,
            data_size=dsize,
            data_count=dcount,
            payload_offset=offset + _HEADER_SIZE,
        )
        offset += _HEADER_SIZE + chunk_bytes


# ---------------------------------------------------------------------------
# Per-chunk parsers
# ---------------------------------------------------------------------------

def _parse_points(data: bytes, hdr: ChunkHeader) -> np.ndarray:
    if hdr.data_size < 12:
        raise PskParseError(f"PNTS0000 dsize too small: {hdr.data_size}")
    arr = np.frombuffer(
        data, dtype=np.float32,
        count=hdr.data_count * 3,
        offset=hdr.payload_offset,
    ).reshape(-1, 3).astype(np.float32, copy=True)
    return arr


def _parse_wedges(data: bytes, hdr: ChunkHeader) -> tuple[np.ndarray, np.ndarray]:
    """Return (point_indices, uv0) arrays of length data_count."""
    # Two layouts in the wild:
    #   16 bytes: uint32 point_idx, float32 u, float32 v, uint8 mat, 3 pad
    #   12 bytes: uint16 point_idx, 2 pad, float32 u, float32 v
    # PSKX (large-mesh) uses the 16-byte layout exclusively. PSK can use either.
    point_idx = np.empty(hdr.data_count, dtype=np.uint32)
    uvs = np.empty((hdr.data_count, 2), dtype=np.float32)
    if hdr.data_size == 16:
        # uint32 + 2 floats + 4 trailing bytes
        for i in range(hdr.data_count):
            base = hdr.payload_offset + i * 16
            point_idx[i] = struct.unpack_from("<I", data, base)[0]
            uvs[i] = struct.unpack_from("<ff", data, base + 4)
    elif hdr.data_size == 12:
        for i in range(hdr.data_count):
            base = hdr.payload_offset + i * 12
            point_idx[i] = struct.unpack_from("<H", data, base)[0]
            uvs[i] = struct.unpack_from("<ff", data, base + 4)
    else:
        raise PskParseError(f"unexpected VTXW0000 dsize: {hdr.data_size}")
    return point_idx, uvs


def _parse_faces(data: bytes, hdr: ChunkHeader, wide: bool) -> np.ndarray:
    """Return an (N, 3) uint32 array of wedge indices.

    `wide=True` for FACE3200 (uint32 wedges), False for FACE0000 (uint16).
    """
    faces = np.empty((hdr.data_count, 3), dtype=np.uint32)
    if wide:
        # FACE3200: 3 * uint32 wedge_idx + 1 byte mat + 1 byte aux + 4 bytes smoothing = 18 bytes minimum,
        # but UE writes it padded to data_size — we trust hdr.data_size for stride.
        stride = hdr.data_size
        for i in range(hdr.data_count):
            base = hdr.payload_offset + i * stride
            faces[i] = struct.unpack_from("<III", data, base)
    else:
        stride = hdr.data_size
        for i in range(hdr.data_count):
            base = hdr.payload_offset + i * stride
            faces[i] = struct.unpack_from("<HHH", data, base)
    return faces


def _decode_material_name(raw: bytes) -> str:
    raw = raw[:64].split(b"\x00", 1)[0]
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        return raw.decode("ascii", errors="replace")


def _parse_materials(data: bytes, hdr: ChunkHeader) -> list[str]:
    if hdr.data_size < 64:
        raise PskParseError(f"MATT0000 dsize too small: {hdr.data_size}")
    names = []
    for i in range(hdr.data_count):
        base = hdr.payload_offset + i * hdr.data_size
        name = _decode_material_name(data[base:base + 64])
        if name:
            names.append(name)
    return names


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _compute_face_normals(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Return per-face normals; verts are wedge positions, faces are wedge indices."""
    v0 = verts[faces[:, 0]]
    v1 = verts[faces[:, 1]]
    v2 = verts[faces[:, 2]]
    n = np.cross(v1 - v0, v2 - v0)
    lens = np.linalg.norm(n, axis=1, keepdims=True)
    # Avoid divide-by-zero on degenerate faces.
    lens = np.where(lens < 1e-12, 1.0, lens)
    return (n / lens).astype(np.float32, copy=False)


def _compute_vertex_normals(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Average face normals into per-wedge normals."""
    face_n = _compute_face_normals(verts, faces)
    out = np.zeros_like(verts, dtype=np.float32)
    np.add.at(out, faces[:, 0], face_n)
    np.add.at(out, faces[:, 1], face_n)
    np.add.at(out, faces[:, 2], face_n)
    lens = np.linalg.norm(out, axis=1, keepdims=True)
    lens = np.where(lens < 1e-12, 1.0, lens)
    return (out / lens).astype(np.float32, copy=False)


def _compute_bounds(verts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    if verts.size == 0:
        z = np.zeros(3, dtype=np.float32)
        return z, z, z, 1.0
    bmin = verts.min(axis=0).astype(np.float32)
    bmax = verts.max(axis=0).astype(np.float32)
    center = ((bmin + bmax) * 0.5).astype(np.float32)
    radius = float(np.linalg.norm(bmax - center))
    if radius < 1e-6:
        radius = 1.0
    return bmin, bmax, center, radius


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_psk(path: Path | str) -> PskMesh:
    """Parse a PSK/PSKX file from disk into a `PskMesh`.

    Raises `PskParseError` on malformed files or `OSError` on I/O failure.
    """
    p = Path(path)
    data = p.read_bytes()

    points: np.ndarray | None = None
    wedge_pts: np.ndarray | None = None
    wedge_uvs: np.ndarray | None = None
    faces: np.ndarray | None = None
    material_names: list[str] = []

    # PSK frequently emits two face chunks (FACE0000 and FACE3200) — prefer
    # FACE3200 when both are present (32-bit wedge indices, supports >65k wedges).
    face_wide_seen = False

    for hdr in _iter_chunks(data):
        cid = hdr.chunk_id
        if cid == "PNTS0000":
            points = _parse_points(data, hdr)
        elif cid == "VTXW0000" or cid == "VTXW3200":
            wedge_pts, wedge_uvs = _parse_wedges(data, hdr)
        elif cid == "FACE0000" and not face_wide_seen:
            faces = _parse_faces(data, hdr, wide=False)
        elif cid == "FACE3200":
            faces = _parse_faces(data, hdr, wide=True)
            face_wide_seen = True
        elif cid == "MATT0000":
            material_names = _parse_materials(data, hdr)
        # All other chunks (skeleton, weights, morphs, extra UVs) are skipped.

    if points is None or wedge_pts is None or wedge_uvs is None or faces is None:
        # An empty mesh is a valid result; only complain when chunks were
        # individually missing — which usually signals truncation.
        empty = (
            (points is None or len(points) == 0) and
            (wedge_pts is None or len(wedge_pts) == 0) and
            (faces is None or len(faces) == 0)
        )
        if not empty:
            raise PskParseError(
                f"PSK missing required chunks "
                f"(points={points is not None}, wedges={wedge_pts is not None}, "
                f"faces={faces is not None})"
            )
        # All-empty: return a zero mesh so the previewer can show "empty mesh".
        return PskMesh(
            verts=np.zeros((0, 3), dtype=np.float32),
            normals=np.zeros((0, 3), dtype=np.float32),
            uv0=np.zeros((0, 2), dtype=np.float32),
            faces=np.zeros((0, 3), dtype=np.uint32),
            material_names=material_names,
        )

    # Defensive: clamp wedge point indices into the points array.
    max_pt = len(points) - 1 if len(points) else 0
    if wedge_pts.size and wedge_pts.max() > max_pt:
        raise PskParseError(
            f"wedge point index {int(wedge_pts.max())} out of range (have {len(points)} points)"
        )
    # Same for face wedge indices.
    max_wedge = len(wedge_pts) - 1 if len(wedge_pts) else 0
    if faces.size and faces.max() > max_wedge:
        raise PskParseError(
            f"face wedge index {int(faces.max())} out of range (have {len(wedge_pts)} wedges)"
        )

    # Expand wedges into a flat per-wedge vertex array.
    verts = points[wedge_pts].astype(np.float32, copy=True)
    uv0 = wedge_uvs.astype(np.float32, copy=True)
    faces_u32 = faces.astype(np.uint32, copy=False)

    normals = _compute_vertex_normals(verts, faces_u32)
    bmin, bmax, center, radius = _compute_bounds(verts)

    return PskMesh(
        verts=verts,
        normals=normals,
        uv0=uv0,
        faces=faces_u32,
        material_names=material_names,
        bounds_min=bmin,
        bounds_max=bmax,
        center=center,
        radius=radius,
    )
