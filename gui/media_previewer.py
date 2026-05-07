"""Unified media preview panel — plays exported audio and video files."""

from __future__ import annotations

import atexit
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QRunnable, QThreadPool, Signal, Slot, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)


class _RmTreeRunnable(QRunnable):
    """Removes a directory tree on a background thread to avoid blocking the GUI."""

    def __init__(self, path: Path, recreate: bool = False):
        super().__init__()
        self._path = Path(path)
        self._recreate = recreate

    def run(self):
        try:
            if self._path.exists():
                shutil.rmtree(self._path, ignore_errors=True)
            if self._recreate:
                self._path.mkdir(parents=True, exist_ok=True)
        except Exception:
            log.exception("Background rmtree failed: %s", self._path)


def _wipe_temp_dir(path: Path) -> None:
    """atexit hook: remove temp dir, swallowing all errors."""
    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _fmt_time(ms: int) -> str:
    """Format milliseconds as mm:ss."""
    if ms < 0:
        ms = 0
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


class MediaPreviewerPanel(QWidget):
    """Right-tab panel for previewing exported audio and video files.

    A single QMediaPlayer drives both modes; the stacked widget swaps
    between an audio art label and a QVideoWidget per loaded file. Two
    independent history lists (audio | video) sit at the bottom — clearing
    one doesn't touch the other, mirrored by per-kind subdirs under the
    shared temp dir so background rmtree only nukes one half.
    """

    log_message = Signal(str, str)

    _MAX_HISTORY = 20
    _AUDIO_EXTS = frozenset({".ogg", ".wav", ".mp3", ".flac", ".m4a", ".wem"})
    _VIDEO_EXTS = frozenset({".mp4", ".webm", ".mov", ".bk2"})
    # bk2 is in _VIDEO_EXTS for routing/history but not _NATIVE_VIDEO_EXTS —
    # Qt can't decode it, so we short-circuit straight to the fallback page.
    _NATIVE_VIDEO_EXTS = frozenset({".mp4", ".webm", ".mov"})

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path: str = ""
        self._current_kind: str = ""  # "audio" | "video" | ""
        self._temp_dir = Path(tempfile.mkdtemp(prefix="ear_media_preview_"))
        # Two subdirs so per-kind Clear can rmtree one without touching the
        # other — the unpacker reuses the parent dir, so existing exports for
        # the other kind survive.
        (self._temp_dir / "audio").mkdir(parents=True, exist_ok=True)
        (self._temp_dir / "video").mkdir(parents=True, exist_ok=True)
        atexit.register(_wipe_temp_dir, self._temp_dir)
        self._last_error_msg: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._placeholder = QLabel("Right-click a media file in the tree to preview")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._placeholder)

        # ── Player area ────────────────────────────────────────────────
        self._player_area = QWidget()
        pa = QVBoxLayout(self._player_area)
        pa.setContentsMargins(0, 0, 0, 0)

        self._name_label = QLabel()
        self._name_label.setWordWrap(True)
        pa.addWidget(self._name_label)

        # Stack: 0 = audio art (no video output), 1 = QVideoWidget,
        # 2 = unsupported-format fallback page with Open Folder button.
        self._stack = QStackedWidget()
        self._audio_art = QLabel("\U0001f3b5")
        self._audio_art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self._audio_art.font()
        f.setPointSize(48)
        self._audio_art.setFont(f)
        self._audio_art.setMinimumHeight(180)
        self._stack.addWidget(self._audio_art)

        self._video_widget = QVideoWidget()
        self._video_widget.setMinimumHeight(180)
        self._stack.addWidget(self._video_widget)

        self._fallback_page = QWidget()
        fp = QVBoxLayout(self._fallback_page)
        fp.setContentsMargins(0, 0, 0, 0)
        self._fallback_label = QLabel("")
        self._fallback_label.setWordWrap(True)
        self._fallback_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fp.addStretch()
        fp.addWidget(self._fallback_label)
        self._open_folder_btn = QPushButton("Open Folder")
        self._open_folder_btn.setFixedWidth(120)
        self._open_folder_btn.clicked.connect(self._open_containing_folder)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._open_folder_btn)
        btn_row.addStretch()
        fp.addLayout(btn_row)
        fp.addStretch()
        self._stack.addWidget(self._fallback_page)
        self._stack.setCurrentIndex(0)
        pa.addWidget(self._stack)

        # Seek bar + time labels
        seek_row = QHBoxLayout()
        self._time_current = QLabel("0:00")
        self._time_current.setFixedWidth(40)
        seek_row.addWidget(self._time_current)

        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setRange(0, 0)
        self._seek.sliderMoved.connect(self._on_seek)
        seek_row.addWidget(self._seek)

        self._time_total = QLabel("0:00")
        self._time_total.setFixedWidth(40)
        seek_row.addWidget(self._time_total)
        pa.addLayout(seek_row)

        # Transport controls
        ctrl_row = QHBoxLayout()
        self._btn_restart = QPushButton("⏮")
        self._btn_restart.setFixedWidth(36)
        self._btn_restart.setToolTip("Restart")
        self._btn_restart.clicked.connect(self._restart)
        ctrl_row.addWidget(self._btn_restart)

        self._btn_play = QPushButton("▶")
        self._btn_play.setFixedWidth(48)
        self._btn_play.setToolTip("Play / Pause")
        self._btn_play.clicked.connect(self._toggle_play)
        ctrl_row.addWidget(self._btn_play)

        self._btn_stop = QPushButton("⏹")
        self._btn_stop.setFixedWidth(36)
        self._btn_stop.setToolTip("Stop")
        self._btn_stop.clicked.connect(self._stop)
        ctrl_row.addWidget(self._btn_stop)

        ctrl_row.addStretch()

        vol_label = QLabel("\U0001f50a")
        ctrl_row.addWidget(vol_label)

        self._volume = QSlider(Qt.Orientation.Horizontal)
        self._volume.setRange(0, 100)
        self._volume.setValue(80)
        self._volume.setFixedWidth(100)
        self._volume.valueChanged.connect(self._on_volume)
        ctrl_row.addWidget(self._volume)

        pa.addLayout(ctrl_row)

        self._status = QLabel()
        pa.addWidget(self._status)

        layout.addWidget(self._player_area)
        self._player_area.setVisible(False)

        # ── History (audio | video) ───────────────────────────────────
        history_row = QHBoxLayout()

        audio_box = QGroupBox("Audio History")
        ab = QVBoxLayout(audio_box)
        self._audio_list = QListWidget()
        self._audio_list.setMaximumHeight(180)
        self._audio_list.itemDoubleClicked.connect(self._on_history_clicked)
        ab.addWidget(self._audio_list)
        clear_audio_btn = QPushButton("Clear")
        clear_audio_btn.setFixedWidth(80)
        clear_audio_btn.clicked.connect(self._clear_audio_history)
        ab.addWidget(clear_audio_btn)
        history_row.addWidget(audio_box)

        video_box = QGroupBox("Video History")
        vb = QVBoxLayout(video_box)
        self._video_list = QListWidget()
        self._video_list.setMaximumHeight(180)
        self._video_list.itemDoubleClicked.connect(self._on_history_clicked)
        vb.addWidget(self._video_list)
        clear_video_btn = QPushButton("Clear")
        clear_video_btn.setFixedWidth(80)
        clear_video_btn.clicked.connect(self._clear_video_history)
        vb.addWidget(clear_video_btn)
        history_row.addWidget(video_box)

        layout.addLayout(history_row)
        layout.addStretch()

        # ── Media backend ─────────────────────────────────────────────
        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(self._volume.value() / 100.0)

        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_widget)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.errorOccurred.connect(self._on_error)
        self._player.mediaStatusChanged.connect(self._on_media_status)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def temp_dir(self) -> Path:
        """Shared temp dir for both audio and video preview files."""
        return self._temp_dir

    def classify(self, path: str) -> str:
        """Return ``"audio"``, ``"video"``, or ``""`` for *path*."""
        suffix = Path(path).suffix.lower()
        if suffix in self._AUDIO_EXTS:
            return "audio"
        if suffix in self._VIDEO_EXTS:
            return "video"
        return ""

    def load_file(self, path: str) -> None:
        """Load *path* — auto-detects mode and routes to the right history."""
        if not Path(path).is_file():
            self._status.setText(f"File not found: {Path(path).name}")
            return

        kind = self.classify(path)
        if not kind:
            self._status.setText(f"Unsupported file type: {Path(path).name}")
            return

        self._current_path = path
        self._current_kind = kind
        self._placeholder.setVisible(False)
        self._player_area.setVisible(True)
        self._name_label.setText(Path(path).name)
        self._status.setText("")
        self._last_error_msg = ""

        # History gets appended before playback starts so a load that fails
        # mid-decode (Bink, broken mp4, …) is still recoverable from the list.
        self._add_to_history(path, kind)

        suffix = Path(path).suffix.lower()
        if kind == "video" and suffix not in self._NATIVE_VIDEO_EXTS:
            # Bink / unsupported container — skip the player entirely; the
            # fallback page already covers Open Folder.
            self._player.stop()
            self._player.setSource(QUrl())
            self._show_fallback(path)
            return

        if kind == "audio":
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentIndex(1)

        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _toggle_play(self):
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _restart(self):
        self._player.setPosition(0)
        self._player.play()

    def _stop(self):
        self._player.stop()

    def _on_seek(self, position: int):
        self._player.setPosition(position)

    def _on_volume(self, value: int):
        self._audio_output.setVolume(value / 100.0)

    # ------------------------------------------------------------------
    # Player signals
    # ------------------------------------------------------------------

    @Slot(QMediaPlayer.PlaybackState)
    def _on_state_changed(self, state: QMediaPlayer.PlaybackState):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._btn_play.setText("⏸")
        else:
            self._btn_play.setText("▶")

    @Slot(int)
    def _on_position(self, ms: int):
        if not self._seek.isSliderDown():
            self._seek.setValue(ms)
        self._time_current.setText(_fmt_time(ms))

    @Slot(int)
    def _on_duration(self, ms: int):
        self._seek.setRange(0, ms)
        self._time_total.setText(_fmt_time(ms))

    @Slot(QMediaPlayer.Error, str)
    def _on_error(self, error: QMediaPlayer.Error, message: str):
        # Drop duplicate error storms (Qt fires errorOccurred repeatedly).
        if message == self._last_error_msg:
            return
        self._last_error_msg = message
        self._status.setText(f"Playback error: {message}")
        self.log_message.emit(f"Media preview error: {message}", "error")
        # Video that Qt couldn't decode → fallback page so the user still
        # has a path forward (Open Folder). Audio errors stay in-place; the
        # status label already explains what happened.
        if self._current_kind == "video" and self._current_path:
            self._show_fallback(self._current_path)

    @Slot(QMediaPlayer.MediaStatus)
    def _on_media_status(self, status: QMediaPlayer.MediaStatus):
        if (status == QMediaPlayer.MediaStatus.InvalidMedia
                and self._current_kind == "video"
                and self._current_path):
            self._show_fallback(self._current_path)

    # ------------------------------------------------------------------
    # Fallback page
    # ------------------------------------------------------------------

    def _show_fallback(self, path: str) -> None:
        self._fallback_label.setText(
            f"Format not playable in app — {Path(path).name}"
        )
        self._stack.setCurrentIndex(2)

    def _open_containing_folder(self) -> None:
        if not self._current_path:
            return
        p = Path(self._current_path)
        if sys.platform == "win32" and p.exists():
            try:
                subprocess.run(
                    ["explorer", "/select,", str(p)],
                    check=False,
                )
                return
            except Exception:
                log.exception("explorer /select failed; falling back to QDesktopServices")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _list_for_kind(self, kind: str) -> QListWidget:
        return self._audio_list if kind == "audio" else self._video_list

    def _add_to_history(self, path: str, kind: str) -> None:
        widget = self._list_for_kind(kind)
        for i in range(widget.count()):
            if widget.item(i).data(Qt.ItemDataRole.UserRole) == path:
                widget.takeItem(i)
                break

        item = QListWidgetItem(Path(path).name)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        widget.insertItem(0, item)

        while widget.count() > self._MAX_HISTORY:
            widget.takeItem(widget.count() - 1)

    def _on_history_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.load_file(path)

    def _clear_audio_history(self) -> None:
        self._clear_history("audio")

    def _clear_video_history(self) -> None:
        self._clear_history("video")

    def _clear_history(self, kind: str) -> None:
        # If the currently-playing item belongs to the kind being cleared,
        # release the player so its temp file isn't locked on Windows.
        if self._current_kind == kind:
            self._player.stop()
            self._player.setSource(QUrl())
        self._list_for_kind(kind).clear()
        QThreadPool.globalInstance().start(
            _RmTreeRunnable(self._temp_dir / kind, recreate=True)
        )
