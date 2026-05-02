"""Audio preview panel — play exported audio files with transport controls."""

from __future__ import annotations

import atexit
import logging
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QRunnable, QThreadPool, Signal, Slot, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
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


class AudioPreviewerPanel(QWidget):
    """Right-tab panel for previewing exported audio files."""

    log_message = Signal(str, str)

    _MAX_HISTORY = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path: str = ""
        self._temp_dir = Path(tempfile.mkdtemp(prefix="ear_audio_preview_"))
        # Last-resort cleanup on interpreter exit (covers crashes / SIGTERM).
        atexit.register(_wipe_temp_dir, self._temp_dir)
        # Debounce errorOccurred — Qt fires it multiple times per failed file.
        self._last_error_msg: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Placeholder ────────────────────────────────────────────────
        self._placeholder = QLabel("Right-click an audio file in the tree to preview")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self._placeholder)

        # ── Player area (hidden until a file is loaded) ────────────────
        self._player_area = QWidget()
        pa = QVBoxLayout(self._player_area)
        pa.setContentsMargins(0, 0, 0, 0)

        # File name
        self._name_label = QLabel()

        self._name_label.setWordWrap(True)
        pa.addWidget(self._name_label)

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
        self._btn_restart = QPushButton("\u23ee")
        self._btn_restart.setFixedWidth(36)
        self._btn_restart.setToolTip("Restart")
        self._btn_restart.clicked.connect(self._restart)
        ctrl_row.addWidget(self._btn_restart)

        self._btn_play = QPushButton("\u25b6")
        self._btn_play.setFixedWidth(48)
        self._btn_play.setToolTip("Play / Pause")
        self._btn_play.clicked.connect(self._toggle_play)
        ctrl_row.addWidget(self._btn_play)

        self._btn_stop = QPushButton("\u23f9")
        self._btn_stop.setFixedWidth(36)
        self._btn_stop.setToolTip("Stop")
        self._btn_stop.clicked.connect(self._stop)
        ctrl_row.addWidget(self._btn_stop)

        ctrl_row.addStretch()

        # Volume
        vol_label = QLabel("\U0001f50a")
        ctrl_row.addWidget(vol_label)

        self._volume = QSlider(Qt.Orientation.Horizontal)
        self._volume.setRange(0, 100)
        self._volume.setValue(80)
        self._volume.setFixedWidth(100)
        self._volume.valueChanged.connect(self._on_volume)
        ctrl_row.addWidget(self._volume)

        pa.addLayout(ctrl_row)

        # Status / error
        self._status = QLabel()

        pa.addWidget(self._status)

        layout.addWidget(self._player_area)
        self._player_area.setVisible(False)

        # ── Recent files ───────────────────────────────────────────────
        recent_label = QLabel("Recent")

        layout.addWidget(recent_label)

        self._recent_list = QListWidget()
        self._recent_list.setMaximumHeight(180)
        self._recent_list.itemDoubleClicked.connect(self._on_recent_clicked)
        layout.addWidget(self._recent_list)

        clear_btn = QPushButton("Clear History")
        clear_btn.setFixedWidth(100)
        clear_btn.clicked.connect(self._clear_history)
        layout.addWidget(clear_btn)

        layout.addStretch()

        # ── Media backend ──────────────────────────────────────────────
        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(self._volume.value() / 100.0)

        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.errorOccurred.connect(self._on_error)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_file(self, path: str):
        """Load and begin playing the audio file at *path*."""
        if not Path(path).is_file():
            self._status.setText(f"File not found: {Path(path).name}")
            return

        self._current_path = path
        self._placeholder.setVisible(False)
        self._player_area.setVisible(True)
        self._name_label.setText(Path(path).name)
        self._status.setText("")
        self._last_error_msg = ""

        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()

        self._add_to_history(path)

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
            self._btn_play.setText("\u23f8")  # pause symbol
        else:
            self._btn_play.setText("\u25b6")  # play symbol

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
        self.log_message.emit(f"Audio preview error: {message}", "error")

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _add_to_history(self, path: str):
        # Remove duplicates
        for i in range(self._recent_list.count()):
            if self._recent_list.item(i).data(Qt.ItemDataRole.UserRole) == path:
                self._recent_list.takeItem(i)
                break

        item = QListWidgetItem(Path(path).name)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        self._recent_list.insertItem(0, item)

        while self._recent_list.count() > self._MAX_HISTORY:
            self._recent_list.takeItem(self._recent_list.count() - 1)

    def _on_recent_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.load_file(path)

    @property
    def temp_dir(self) -> Path:
        """Temp directory for audio preview files — cleared with history."""
        return self._temp_dir

    def _clear_history(self):
        # Stop playback and release the source so files in the temp dir
        # aren't locked when we try to remove them on Windows.
        self._player.stop()
        self._player.setSource(QUrl())
        self._recent_list.clear()
        # Remove temp preview files asynchronously so a slow filesystem
        # doesn't freeze the UI.
        QThreadPool.globalInstance().start(
            _RmTreeRunnable(self._temp_dir, recreate=True)
        )
