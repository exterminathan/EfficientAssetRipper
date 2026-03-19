"""Blend Combiner panel — select multiple .blend files and merge them."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import config

log = logging.getLogger(__name__)

from _base import base_dir

COMBINE_SCRIPT = str(
    base_dir() / "blender" / "combine_blends.py"
)


class CombineWorker(QThread):
    """Run Blender in background to combine .blend files."""

    progress = Signal(int, int, str)  # current, total, message
    finished = Signal(bool, str)      # success, message

    def __init__(
        self,
        blender_exe: str,
        blend_files: list[str],
        output_path: str,
        spacing: float,
        columns: int,
        parent=None,
    ):
        super().__init__(parent)
        self._blender_exe = blender_exe
        self._blend_files = blend_files
        self._output_path = output_path
        self._spacing = spacing
        self._columns = columns

    def run(self):
        manifest = {
            "blend_files": self._blend_files,
            "output_path": self._output_path,
            "spacing": self._spacing,
            "columns": self._columns,
        }

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="ear_combine_")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)

            cmd = [
                self._blender_exe,
                "--background",
                "--python", COMBINE_SCRIPT,
                "--", tmp_path,
            ]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                ),
            )

            for line in proc.stdout:
                line = line.strip()
                if not line.startswith("##COMBINE_STATUS##"):
                    continue
                try:
                    data = json.loads(line[len("##COMBINE_STATUS##"):])
                except json.JSONDecodeError:
                    continue

                st = data.get("status")
                if st == "progress":
                    self.progress.emit(
                        data.get("index", 0),
                        data.get("total", 0),
                        data.get("file", ""),
                    )
                elif st == "completed":
                    self.finished.emit(
                        True,
                        f"Combined {data.get('total_objects', '?')} objects "
                        f"→ {data.get('output', '')}",
                    )
                    return
                elif st == "error":
                    self.finished.emit(False, data.get("message", "Unknown error"))
                    return

            proc.wait()
            if proc.returncode != 0:
                stderr = proc.stderr.read() if proc.stderr else ""
                self.finished.emit(False, f"Blender exited with code {proc.returncode}: {stderr[:500]}")
            else:
                self.finished.emit(True, f"Saved → {self._output_path}")
        except Exception as e:
            self.finished.emit(False, str(e))
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class BlendCombinerPanel(QWidget):
    """Panel for selecting and combining .blend files."""

    log_message = Signal(str, str)  # message, level

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: CombineWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Header
        header = QLabel("<b>Blend Combiner</b>  — Merge multiple .blend files into one")
        layout.addWidget(header)

        # File list
        list_group = QGroupBox("Blend Files")
        list_layout = QVBoxLayout(list_group)

        self._file_list = QListWidget()
        self._file_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
        )
        self._file_list.setAlternatingRowColors(True)
        list_layout.addWidget(self._file_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Files...")
        add_btn.clicked.connect(self._add_files)
        btn_row.addWidget(add_btn)

        add_folder_btn = QPushButton("Add Folder...")
        add_folder_btn.clicked.connect(self._add_folder)
        btn_row.addWidget(add_folder_btn)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(remove_btn)

        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self._file_list.clear)
        btn_row.addWidget(clear_btn)

        btn_row.addStretch()
        list_layout.addLayout(btn_row)
        layout.addWidget(list_group)

        # Options
        opts_group = QGroupBox("Options")
        opts_layout = QHBoxLayout(opts_group)

        opts_layout.addWidget(QLabel("Spacing:"))
        self._spacing = QDoubleSpinBox()
        self._spacing.setRange(0.1, 10000.0)
        self._spacing.setValue(1000.0)
        self._spacing.setSuffix(" units")
        self._spacing.setDecimals(1)
        opts_layout.addWidget(self._spacing)

        opts_layout.addWidget(QLabel("Columns:"))
        self._columns = QSpinBox()
        self._columns.setRange(0, 1000)
        self._columns.setValue(3)
        self._columns.setSpecialValueText("Auto")
        self._columns.setToolTip("0 = auto (square grid)")
        opts_layout.addWidget(self._columns)

        opts_layout.addStretch()
        layout.addWidget(opts_group)

        # Action
        action_row = QHBoxLayout()

        self._combine_btn = QPushButton("Combine")
        self._combine_btn.setMinimumHeight(36)
        self._combine_btn.clicked.connect(self._start_combine)
        action_row.addWidget(self._combine_btn)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        action_row.addWidget(self._progress)

        layout.addLayout(action_row)

        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        layout.addStretch()

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Blend Files",
            config.get("output_dir") or "",
            "Blend Files (*.blend)",
        )
        for f in files:
            if not self._has_file(f):
                self._file_list.addItem(f)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder Containing .blend Files",
            config.get("output_dir") or "",
        )
        if not folder:
            return
        count = 0
        for root, _dirs, fnames in os.walk(folder):
            for fn in sorted(fnames):
                if fn.lower().endswith(".blend"):
                    full = os.path.join(root, fn)
                    if not self._has_file(full):
                        self._file_list.addItem(full)
                        count += 1
        self._status_label.setText(f"Added {count} files from {folder}")

    def _remove_selected(self):
        for item in self._file_list.selectedItems():
            self._file_list.takeItem(self._file_list.row(item))

    def _has_file(self, path: str) -> bool:
        for i in range(self._file_list.count()):
            if self._file_list.item(i).text() == path:
                return True
        return False

    def _get_all_files(self) -> list[str]:
        return [
            self._file_list.item(i).text()
            for i in range(self._file_list.count())
        ]

    # ------------------------------------------------------------------
    # Combine
    # ------------------------------------------------------------------

    def _start_combine(self):
        files = self._get_all_files()
        if not files:
            QMessageBox.information(self, "No Files", "Add .blend files first.")
            return

        blender_exe = config.get("blender_exe")
        if not blender_exe:
            QMessageBox.warning(
                self, "Blender Not Set",
                "Set the Blender path in Settings first.",
            )
            return

        output_path, _ = QFileDialog.getSaveFileName(
            self, "Save Combined Blend",
            str(Path(config.get("output_dir") or ".") / "combined.blend"),
            "Blend Files (*.blend)",
        )
        if not output_path:
            return

        self._combine_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, len(files))
        self._progress.setValue(0)
        self._status_label.setText("Combining...")

        self.log_message.emit(
            f"Combining {len(files)} .blend files → {output_path}", "info"
        )

        self._worker = CombineWorker(
            blender_exe=blender_exe,
            blend_files=files,
            output_path=output_path,
            spacing=self._spacing.value(),
            columns=self._columns.value(),
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    @Slot(int, int, str)
    def _on_progress(self, current: int, total: int, filename: str):
        self._progress.setValue(current)
        self._status_label.setText(f"[{current}/{total}] {filename}")

    @Slot(bool, str)
    def _on_finished(self, success: bool, message: str):
        self._combine_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._status_label.setText(message)
        level = "success" if success else "error"
        self.log_message.emit(f"Combine: {message}", level)
