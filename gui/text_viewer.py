"""Read-only text viewer panel for uasset / props file contents."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class TextViewer(QWidget):
    """Tab panel that displays formatted text content (e.g. uasset properties)."""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Header
        header = QHBoxLayout()
        self._title_label = QLabel("No file loaded")

        header.addWidget(self._title_label, stretch=1)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search...")
        self._search.setFixedWidth(200)
        self._search.textChanged.connect(self._on_search)
        header.addWidget(self._search)

        self._match_label = QLabel("")
        header.addWidget(self._match_label)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedWidth(60)
        self._clear_btn.clicked.connect(self.clear)
        header.addWidget(self._clear_btn)

        layout.addLayout(header)

        # Text area
        self._editor = QPlainTextEdit()
        self._editor.setReadOnly(True)
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Cascadia Code", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._editor.setFont(font)
        layout.addWidget(self._editor)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_text(self, title: str, text: str):
        """Display *text* in the viewer with the given *title*."""
        self._title_label.setText(title)
        self._editor.setPlainText(text)
        self._search.clear()
        self._match_label.setText("")

    def clear(self):
        """Clear the viewer."""
        self._title_label.setText("No file loaded")
        self._editor.clear()
        self._search.clear()
        self._match_label.setText("")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _on_search(self, text: str):
        """Highlight and jump to search matches."""
        from PySide6.QtGui import QTextCharFormat, QColor, QTextCursor

        # Clear previous highlights
        cursor = self._editor.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        plain = QTextCharFormat()
        cursor.setCharFormat(plain)
        cursor.clearSelection()
        self._editor.setTextCursor(cursor)

        if not text:
            self._match_label.setText("")
            return

        # Highlight all matches
        highlight = QTextCharFormat()
        highlight.setBackground(QColor(100, 80, 0))
        highlight.setForeground(QColor(255, 255, 255))

        doc = self._editor.document()
        cursor = QTextCursor(doc)
        count = 0
        first_match = None

        while True:
            cursor = doc.find(text, cursor)
            if cursor.isNull():
                break
            if first_match is None:
                first_match = QTextCursor(cursor)
            cursor.mergeCharFormat(highlight)
            count += 1

        self._match_label.setText(f"{count} match{'es' if count != 1 else ''}")

        # Scroll to first match
        if first_match is not None:
            self._editor.setTextCursor(first_match)
            self._editor.centerCursor()
