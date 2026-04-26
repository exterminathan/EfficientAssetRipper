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
    # _on_search runs as the slot for textChanged — match label should now reflect 3
    assert "3" in w._match_label.text()
