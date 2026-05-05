"""Qt-tier tests for `gui.mesh_previewer.MeshPreviewerPanel`.

Avoids touching real GL state — we never trigger paintGL or showEvent. The
viewport widget is constructed but never shown, so its OpenGL context is
never created and the shader / VBO code paths stay dormant.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from gui.mesh_previewer import MeshPreviewerPanel, _MeshGLView

pytestmark = pytest.mark.qt


# Reuse the synthetic-PSK builder verbatim so this test stays self-contained.
def _chunk_header(chunk_id: str, type_flag: int, data_size: int, data_count: int) -> bytes:
    return chunk_id.encode("ascii").ljust(20, b"\x00") + struct.pack(
        "<III", type_flag, data_size, data_count
    )


def _tiny_psk_bytes() -> bytes:
    out = bytearray()
    out += _chunk_header("ACTRHEAD", 0, 0, 0)
    out += _chunk_header("PNTS0000", 0, 12, 3)
    for x, y, z in [(0, 0, 0), (1, 0, 0), (0, 1, 0)]:
        out += struct.pack("<fff", x, y, z)
    out += _chunk_header("VTXW0000", 0, 16, 3)
    for pi, u, v in [(0, 0.0, 0.0), (1, 1.0, 0.0), (2, 0.0, 1.0)]:
        out += struct.pack("<I", pi) + struct.pack("<ff", u, v) + b"\x00" * 4
    out += _chunk_header("FACE0000", 0, 12, 1)
    out += struct.pack("<HHH", 0, 1, 2) + b"\x00" * 6
    return bytes(out)


def test_mesh_previewer_constructs_empty(qtbot):
    p = MeshPreviewerPanel()
    qtbot.addWidget(p)
    assert p._gl is not None
    # Default mode is "flat"; mode toggle buttons reflect it.
    assert p._gl.render_mode == "flat"
    assert p._btn_flat.isChecked()
    assert not p._btn_uv.isChecked()
    assert not p._btn_wire.isChecked()


def test_mesh_previewer_mode_buttons_toggle_state(qtbot):
    p = MeshPreviewerPanel()
    qtbot.addWidget(p)

    p._gl.set_render_mode("uv")
    assert p._gl.render_mode == "uv"
    assert p._btn_uv.isChecked()
    assert not p._btn_flat.isChecked()

    p._gl.set_render_mode("wire")
    assert p._gl.render_mode == "wire"
    assert p._btn_wire.isChecked()


def test_mesh_previewer_load_missing_file_shows_placeholder(qtbot, tmp_path: Path):
    p = MeshPreviewerPanel()
    qtbot.addWidget(p)

    p.load_psk(str(tmp_path / "does_not_exist.psk"))
    assert p._stack.currentWidget() is p._placeholder
    assert "Not found" in p._status_name.text() or "not found" in p._status_name.text()


def test_mesh_previewer_load_valid_psk_emits_mesh_loaded(qtbot, tmp_path: Path):
    p = MeshPreviewerPanel()
    qtbot.addWidget(p)

    psk = tmp_path / "tri.psk"
    psk.write_bytes(_tiny_psk_bytes())

    # The loader runs on QThreadPool — wait for the GL view's mesh_loaded
    # signal to fire (it fires once the mesh data arrives, even if the GL
    # context never initialises because the widget isn't shown).
    with qtbot.waitSignal(p._gl.mesh_loaded, timeout=4000) as blocker:
        p.load_psk(str(psk))
    vert_count, tri_count = blocker.args
    assert tri_count == 1
    assert vert_count == 3
    # Status strip reflects the loaded mesh.
    assert "verts" in p._status_stats.text()
    assert "tris" in p._status_stats.text()


def test_mesh_previewer_clear_resets_to_placeholder(qtbot, tmp_path: Path):
    p = MeshPreviewerPanel()
    qtbot.addWidget(p)

    psk = tmp_path / "tri.psk"
    psk.write_bytes(_tiny_psk_bytes())
    with qtbot.waitSignal(p._gl.mesh_loaded, timeout=4000):
        p.load_psk(str(psk))

    p.clear()
    assert p._stack.currentWidget() is p._placeholder
    assert p._status_name.text() == ""


def test_meshglview_orbit_camera_unlimited(qtbot):
    """Quaternion orbit must support full 360° rotation in any direction —
    no clamping, no degenerate state when crossing the poles."""
    import numpy as np
    from gui.mesh_previewer import (
        _quat_axis_angle,
        _quat_mul,
        _quat_normalize,
    )

    v = _MeshGLView()
    qtbot.addWidget(v)

    # Spam pitch all the way past the pole and back. Without clamping and
    # with quaternion accumulation, orientation must stay normalized.
    for _ in range(2000):
        pitch = _quat_axis_angle((1.0, 0.0, 0.0), 0.05)
        v._orientation = _quat_normalize(_quat_mul(v._orientation, pitch))

    norm = float(np.linalg.norm(v._orientation))
    assert abs(norm - 1.0) < 1e-6, f"quaternion drifted: |q|={norm}"

    # And spam yaw the same way — the camera must accept any total angle.
    for _ in range(2000):
        yaw = _quat_axis_angle((0.0, 1.0, 0.0), 0.05)
        v._orientation = _quat_normalize(_quat_mul(yaw, v._orientation))

    norm = float(np.linalg.norm(v._orientation))
    assert abs(norm - 1.0) < 1e-6


def test_meshglview_default_orientation_is_unit(qtbot):
    import numpy as np
    v = _MeshGLView()
    qtbot.addWidget(v)
    assert abs(float(np.linalg.norm(v._orientation)) - 1.0) < 1e-6


def test_meshglview_reset_view_restores_default_orientation(qtbot):
    import numpy as np
    from gui.mesh_previewer import _quat_axis_angle

    v = _MeshGLView()
    qtbot.addWidget(v)
    default = v._orientation.copy()
    v._orientation = _quat_axis_angle((1.0, 0.0, 0.0), 1.234)
    assert not np.allclose(v._orientation, default)

    v.reset_view()
    np.testing.assert_allclose(v._orientation, default, atol=1e-9)
