"""Tests for `gui.media_previewer.MediaPreviewerPanel`.

Covers the unified audio/video preview tab — temp-dir layout, kind classification,
fallback for unplayable formats (Bink), per-kind history, error debounce.
We don't actually play media — Qt Multimedia isn't available in CI without a
sound stack, and the QVideoWidget path doesn't decode in offscreen mode either.
"""

from __future__ import annotations

import pytest

from PySide6.QtMultimedia import QMediaPlayer

from gui.media_previewer import MediaPreviewerPanel

pytestmark = pytest.mark.qt


def test_constructs_with_temp_dir_and_subfolders(qtbot):
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    assert w.temp_dir.exists()
    # Per-kind subdirs so the per-list Clear can rmtree only one half.
    assert (w.temp_dir / "audio").is_dir()
    assert (w.temp_dir / "video").is_dir()


def test_classify_audio_extensions(qtbot):
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    for ext in (".ogg", ".wav", ".mp3", ".flac", ".m4a", ".wem"):
        assert w.classify(f"foo{ext}") == "audio"


def test_classify_video_extensions(qtbot):
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    for ext in (".mp4", ".webm", ".mov", ".bk2"):
        assert w.classify(f"foo{ext}") == "video"


def test_classify_unsupported(qtbot):
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    assert w.classify("foo.psk") == ""
    assert w.classify("foo.uasset") == ""


def test_load_audio_keeps_video_widget_hidden(qtbot, tmp_path):
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    f = tmp_path / "song.ogg"
    f.write_bytes(b"\x00" * 16)
    w.load_file(str(f))
    # Stack index 0 = audio art label.
    assert w._stack.currentIndex() == 0
    # Audio history got the entry; video list is untouched.
    assert w._audio_list.count() == 1
    assert w._video_list.count() == 0


def test_load_native_video_shows_video_widget(qtbot, tmp_path):
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 16)
    w.load_file(str(f))
    # Stack index 1 = QVideoWidget.
    assert w._stack.currentIndex() == 1
    assert w._video_list.count() == 1
    assert w._audio_list.count() == 0


def test_load_bk2_short_circuits_to_fallback(qtbot, tmp_path):
    """Bink can't be decoded by Qt — we route to the fallback page up front
    instead of letting QMediaPlayer fire spurious errors. Entry still shows
    in video history so the user can find it / open the folder."""
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    f = tmp_path / "intro.bk2"
    f.write_bytes(b"\x00" * 16)
    w.load_file(str(f))
    # Stack index 2 = fallback page.
    assert w._stack.currentIndex() == 2
    assert "intro.bk2" in w._fallback_label.text()
    assert w._video_list.count() == 1


def test_load_missing_file_reports_status(qtbot, tmp_path):
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    w.load_file(str(tmp_path / "nope.mp4"))
    assert "not found" in w._status.text().lower()
    assert w._video_list.count() == 0


def test_history_per_kind_independent(qtbot, tmp_path):
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    a = tmp_path / "song.ogg"; a.write_bytes(b"\x00")
    v = tmp_path / "clip.mp4"; v.write_bytes(b"\x00")
    w.load_file(str(a))
    w.load_file(str(v))
    assert w._audio_list.count() == 1
    assert w._video_list.count() == 1


def test_clear_audio_history_leaves_video_intact(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QThreadPool

    submitted: list = []
    real_start = QThreadPool.start

    def _spy(self, runnable, *a, **kw):
        submitted.append(runnable)
        return real_start(self, runnable, *a, **kw)

    monkeypatch.setattr(QThreadPool, "start", _spy)

    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    a = tmp_path / "song.ogg"; a.write_bytes(b"\x00")
    v = tmp_path / "clip.mp4"; v.write_bytes(b"\x00")
    w.load_file(str(a))
    w.load_file(str(v))

    w._clear_audio_history()
    assert w._audio_list.count() == 0
    assert w._video_list.count() == 1
    assert any(type(r).__name__ == "_RmTreeRunnable" for r in submitted)


def test_clear_video_history_leaves_audio_intact(qtbot, tmp_path):
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    a = tmp_path / "song.ogg"; a.write_bytes(b"\x00")
    v = tmp_path / "clip.mp4"; v.write_bytes(b"\x00")
    w.load_file(str(a))
    w.load_file(str(v))

    w._clear_video_history()
    assert w._video_list.count() == 0
    assert w._audio_list.count() == 1


def test_error_signal_debounced(qtbot):
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)

    captured: list = []
    w.log_message.connect(lambda msg, lvl: captured.append((msg, lvl)))

    w._on_error(QMediaPlayer.Error.ResourceError, "boom")
    w._on_error(QMediaPlayer.Error.ResourceError, "boom")
    w._on_error(QMediaPlayer.Error.ResourceError, "boom")

    assert len(captured) == 1


def test_video_error_swaps_to_fallback(qtbot, tmp_path):
    """A video that QMediaPlayer can't decode should land on the fallback page
    so the user still has a path forward (Open Folder)."""
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    f = tmp_path / "clip.mp4"; f.write_bytes(b"\x00")
    w.load_file(str(f))
    # Force the error path.
    w._on_error(QMediaPlayer.Error.ResourceError, "decode failed")
    assert w._stack.currentIndex() == 2


def test_invalid_media_status_swaps_to_fallback(qtbot, tmp_path):
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    f = tmp_path / "clip.mp4"; f.write_bytes(b"\x00")
    w.load_file(str(f))
    w._on_media_status(QMediaPlayer.MediaStatus.InvalidMedia)
    assert w._stack.currentIndex() == 2


def test_double_click_reloads_from_history(qtbot, tmp_path):
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    a = tmp_path / "song.ogg"; a.write_bytes(b"\x00")
    w.load_file(str(a))
    item = w._audio_list.item(0)
    assert item is not None
    # Reset stack to simulate user navigating away then double-clicking.
    w._stack.setCurrentIndex(2)
    w._on_history_clicked(item)
    assert w._stack.currentIndex() == 0  # back to audio art


def test_error_resets_after_load_file(qtbot, tmp_path):
    w = MediaPreviewerPanel()
    qtbot.addWidget(w)
    captured: list = []
    w.log_message.connect(lambda msg, lvl: captured.append((msg, lvl)))

    w._on_error(QMediaPlayer.Error.ResourceError, "boom")
    assert len(captured) == 1

    f = tmp_path / "song.ogg"; f.write_bytes(b"\x00")
    w.load_file(str(f))

    w._on_error(QMediaPlayer.Error.ResourceError, "boom")
    assert len(captured) == 2
