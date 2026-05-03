"""Smoke tests for `gui.widgets` custom widgets."""

from __future__ import annotations

import pytest

from PySide6.QtWidgets import QVBoxLayout, QLabel

from gui.widgets import CollapsibleSection, PathPicker, ZoomableTree

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


def test_path_picker_setText_text_round_trip(qtbot):
    p = PathPicker(mode="folder")
    qtbot.addWidget(p)
    p.setText(r"C:\Picked")
    assert p.text() == r"C:\Picked"


def test_path_picker_emits_changed_signal(qtbot):
    p = PathPicker(mode="folder")
    qtbot.addWidget(p)
    with qtbot.waitSignal(p.changed, timeout=1000) as sig:
        p.setText(r"C:\NewPath")
    assert sig.args == [r"C:\NewPath"]


def test_path_picker_browse_file_mode_uses_open_file_dialog(qtbot, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    captured: dict = {}

    def _fake(parent, title, start, filt):
        captured["called"] = (parent, title, start, filt)
        return (r"C:\From\Mock\file.exe", filt)

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(_fake))
    p = PathPicker(mode="file", filter_str="Exe (*.exe)")
    qtbot.addWidget(p)
    p._browse()
    assert captured.get("called") is not None
    assert p.text() == r"C:\From\Mock\file.exe"
