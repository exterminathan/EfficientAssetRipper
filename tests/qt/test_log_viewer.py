"""Tests for `gui.log_viewer.LogViewer` (cap + append behavior)."""

from __future__ import annotations

import pytest

import gui.log_viewer as lv
from gui.log_viewer import LogViewer

pytestmark = pytest.mark.qt


def test_log_viewer_constructs(qtbot):
    w = LogViewer()
    qtbot.addWidget(w)
    assert w._entries == []


def test_append_adds_entry(qtbot):
    w = LogViewer()
    qtbot.addWidget(w)
    w.append("hello", "info")
    assert ("hello", "info") in w._entries
    assert "hello" in w._text.toPlainText()


def test_append_caps_in_memory_entries(qtbot, monkeypatch):
    """The in-memory backlog must be bounded so spam can't OOM the GUI."""
    monkeypatch.setattr(lv, "_MAX_LOG_ENTRIES", 50)
    w = LogViewer()
    qtbot.addWidget(w)
    for i in range(100):
        w.append(f"msg-{i}", "info")
    # Entries are capped at the configured maximum.
    assert len(w._entries) == 50
    # Oldest entries should have been dropped.
    assert ("msg-0", "info") not in w._entries
    assert ("msg-99", "info") in w._entries


def test_text_document_block_count_is_capped(qtbot):
    """The QTextEdit document also caps blocks so render memory is bounded."""
    w = LogViewer()
    qtbot.addWidget(w)
    # The cap matches the module-level constant.
    assert w._text.document().maximumBlockCount() == lv._MAX_LOG_ENTRIES


def test_clear_resets_entries(qtbot):
    w = LogViewer()
    qtbot.addWidget(w)
    w.append("a", "info")
    w.append("b", "warning")
    w.clear()
    assert w._entries == []
    assert w._text.toPlainText() == ""
