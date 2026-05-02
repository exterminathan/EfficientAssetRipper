"""Color-coded log viewer widget with filtering."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QFont, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import gui.theme as theme


def _level_colors():
    c = theme.current_scheme()
    return {
        "info":    QColor(c["text_primary"]),
        "success": QColor(c["success"]),
        "warning": QColor(c["warning"]),
        "error":   QColor(c["error"]),
    }


_MAX_LOG_ENTRIES = 5000


class LogViewer(QWidget):
    """Read-only, color-coded log output panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[tuple[str, str]] = []  # (message, level)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Log"))
        toolbar.addStretch()

        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["All", "Info", "Success", "Warning", "Error"])
        self._filter_combo.currentTextChanged.connect(self._apply_filter)
        toolbar.addWidget(QLabel("Filter:"))
        toolbar.addWidget(self._filter_combo)

        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setFixedWidth(60)
        self._copy_btn.clicked.connect(self._copy_log)
        toolbar.addWidget(self._copy_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setFixedWidth(60)
        self._save_btn.clicked.connect(self._save_log)
        toolbar.addWidget(self._save_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedWidth(60)
        self._clear_btn.clicked.connect(self.clear)
        toolbar.addWidget(self._clear_btn)

        layout.addLayout(toolbar)

        # Log text area
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Cascadia Code", 9))
        # Bound the on-screen text so an infinite spam stream can't OOM the GUI.
        self._text.document().setMaximumBlockCount(_MAX_LOG_ENTRIES)

        layout.addWidget(self._text)

    @Slot(str, str)
    def append(self, message: str, level: str = "info"):
        """Append a log message with color coding."""
        self._entries.append((message, level))
        # Cap the in-memory backlog too (drop oldest).
        if len(self._entries) > _MAX_LOG_ENTRIES:
            del self._entries[: len(self._entries) - _MAX_LOG_ENTRIES]

        # Check filter
        current_filter = self._filter_combo.currentText().lower()
        if current_filter != "all" and current_filter != level:
            return

        self._append_formatted(message, level)

    def _append_formatted(self, message: str, level: str):
        colors = _level_colors()
        color = colors.get(level, colors["info"])
        fmt = QTextCharFormat()
        fmt.setForeground(color)

        cursor = self._text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(message + "\n", fmt)
        self._text.setTextCursor(cursor)
        self._text.ensureCursorVisible()

    def _apply_filter(self, filter_text: str):
        self._text.clear()
        level_filter = filter_text.lower()
        for message, level in self._entries:
            if level_filter == "all" or level_filter == level:
                self._append_formatted(message, level)

    def clear(self):
        self._entries.clear()
        self._text.clear()

    def _copy_log(self):
        text = "\n".join(msg for msg, _ in self._entries)
        QApplication.clipboard().setText(text)

    def _save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Log", "batch_log.txt", "Text Files (*.txt)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                for msg, level in self._entries:
                    f.write(f"[{level.upper()}] {msg}\n")
