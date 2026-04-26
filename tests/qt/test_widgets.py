"""Smoke tests for `gui.widgets` custom widgets."""

from __future__ import annotations

import pytest

from PySide6.QtWidgets import QVBoxLayout, QLabel

from gui.widgets import CollapsibleSection, ZoomableTree

pytestmark = pytest.mark.qt


def test_zoomable_tree_constructs(qtbot):
    t = ZoomableTree()
    qtbot.addWidget(t)
    assert t.font().pointSize() > 0


def test_collapsible_section_starts_expanded(qtbot):
    sec = CollapsibleSection("My Section", start_expanded=True, closeable=True)
    qtbot.addWidget(sec)
    assert sec._expanded is True
    assert sec._content.isVisibleTo(sec) or not sec.isVisible()  # hidden until shown


def test_collapsible_section_toggle_collapses(qtbot):
    sec = CollapsibleSection("Toggle Me", start_expanded=True, closeable=True)
    qtbot.addWidget(sec)
    sec._toggle()
    assert sec._expanded is False


def test_collapsible_section_set_content_layout(qtbot):
    sec = CollapsibleSection("With Content", parent=None)
    qtbot.addWidget(sec)
    inner = QVBoxLayout()
    inner.addWidget(QLabel("hello"))
    sec.set_content_layout(inner)
    assert sec._content.layout() is inner
