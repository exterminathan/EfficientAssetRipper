"""Tests for `gui.tga_previewer.TGAPreviewerPanel` background loader."""

from __future__ import annotations

import pytest

from PySide6.QtGui import QImage

from gui.tga_previewer import TGAPreviewerPanel, _SUPPORTED_EXTS

pytestmark = pytest.mark.qt


def test_constructs(qtbot):
    w = TGAPreviewerPanel()
    qtbot.addWidget(w)
    assert w._load_token == 0


def test_supported_extensions_cover_common_formats():
    for ext in (".tga", ".png", ".jpg", ".jpeg", ".bmp", ".dds"):
        assert ext in _SUPPORTED_EXTS


def test_load_image_bumps_token_and_shows_loading_label(qtbot, tmp_path, monkeypatch):
    """Load should immediately update UI, then dispatch decoding off-thread."""
    from PySide6.QtCore import QThreadPool

    # Don't actually run the decoder — just record that something was submitted.
    submitted: list = []
    monkeypatch.setattr(
        QThreadPool, "start",
        lambda self, runnable, *a, **kw: submitted.append(runnable),
    )

    w = TGAPreviewerPanel()
    qtbot.addWidget(w)
    p = tmp_path / "x.png"
    p.write_bytes(b"placeholder")

    w._load_image(str(p))
    assert w._load_token == 1
    assert "Loading" in w._label.text()
    assert submitted, "expected a runnable to be submitted"


def test_stale_load_results_dropped(qtbot):
    """A loaded result whose token is stale must not update the scene."""
    w = TGAPreviewerPanel()
    qtbot.addWidget(w)
    w._load_token = 5  # simulate a newer in-flight load

    img = QImage(2, 2, QImage.Format.Format_RGBA8888)
    img.fill(0)
    # Fire the slot directly with a stale token.
    w._on_load_done(token=2, img=img, w=2, h=2, name="old.png")

    assert w._pixmap_item is None  # nothing was rendered


def test_load_failed_shows_error_label(qtbot):
    w = TGAPreviewerPanel()
    qtbot.addWidget(w)
    w._load_token = 1
    w._on_load_failed(token=1, name="bad.tga", error="decode error")
    assert "Failed" in w._label.text()
    assert "decode error" in w._label.text()
