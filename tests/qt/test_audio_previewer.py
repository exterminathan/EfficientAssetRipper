"""Tests for `gui.audio_previewer.AudioPreviewerPanel`.

Covers the new background-rmtree + error-debounce behavior. We don't actually
play any audio — Qt Multimedia isn't available in CI without a sound stack.
"""

from __future__ import annotations

import pytest

from PySide6.QtMultimedia import QMediaPlayer

from gui.audio_previewer import AudioPreviewerPanel

pytestmark = pytest.mark.qt


def test_constructs_with_temp_dir(qtbot):
    w = AudioPreviewerPanel()
    qtbot.addWidget(w)
    assert w.temp_dir.exists()


def test_clear_history_uses_background_rmtree(qtbot, monkeypatch):
    """`_clear_history` must dispatch the rmtree to QThreadPool."""
    from PySide6.QtCore import QThreadPool

    submitted: list = []

    real_start = QThreadPool.start

    def _spy(self, runnable, *a, **kw):
        submitted.append(runnable)
        return real_start(self, runnable, *a, **kw)

    monkeypatch.setattr(QThreadPool, "start", _spy)

    w = AudioPreviewerPanel()
    qtbot.addWidget(w)
    w._clear_history()

    assert any(type(r).__name__ == "_RmTreeRunnable" for r in submitted), (
        f"expected an _RmTreeRunnable to be submitted, got: {submitted}"
    )


def test_error_signal_debounced(qtbot):
    """Repeated identical errors should only emit one log_message."""
    w = AudioPreviewerPanel()
    qtbot.addWidget(w)

    captured: list = []
    w.log_message.connect(lambda msg, lvl: captured.append((msg, lvl)))

    w._on_error(QMediaPlayer.Error.ResourceError, "boom")
    w._on_error(QMediaPlayer.Error.ResourceError, "boom")
    w._on_error(QMediaPlayer.Error.ResourceError, "boom")

    # Three identical callbacks → one log emit.
    assert len(captured) == 1


def test_error_resets_after_load_file(qtbot, tmp_path, monkeypatch):
    """Loading a fresh file should clear the error-debounce state."""
    w = AudioPreviewerPanel()
    qtbot.addWidget(w)

    captured: list = []
    w.log_message.connect(lambda msg, lvl: captured.append((msg, lvl)))

    w._on_error(QMediaPlayer.Error.ResourceError, "boom")
    assert len(captured) == 1

    # Create a real file so load_file passes the is_file check.
    fake_file = tmp_path / "test.wav"
    fake_file.write_bytes(b"\x00" * 16)
    w.load_file(str(fake_file))

    # After loading, the same error should fire again.
    w._on_error(QMediaPlayer.Error.ResourceError, "boom")
    assert len(captured) == 2
