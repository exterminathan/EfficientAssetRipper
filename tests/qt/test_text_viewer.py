"""Smoke tests for `gui.text_viewer.TextViewer`."""

from __future__ import annotations

import pytest

from gui.text_viewer import TextViewer

pytestmark = pytest.mark.qt


def test_text_viewer_constructs(qtbot):
    w = TextViewer()
    qtbot.addWidget(w)
    assert w._title_label.text() == "No file loaded"


def test_show_text_displays_content(qtbot):
    w = TextViewer()
    qtbot.addWidget(w)
    w.show_text("My Title", "hello world")
    assert w._title_label.text() == "My Title"
    assert "hello world" in w._editor.toPlainText()


def test_clear_resets(qtbot):
    w = TextViewer()
    qtbot.addWidget(w)
    w.show_text("X", "abc")
    w.clear()
    assert w._title_label.text() == "No file loaded"
    assert w._editor.toPlainText() == ""


def test_search_highlights_match_count(qtbot):
    w = TextViewer()
    qtbot.addWidget(w)
    w.show_text("X", "alpha beta alpha gamma alpha")
    w._search.setText("alpha")
    # The search is debounced via QTimer (150 ms); wait for the match-count
    # label to reflect the result instead of relying on synchronous updates.
    qtbot.waitUntil(lambda: "3" in w._match_label.text(), timeout=2000)
