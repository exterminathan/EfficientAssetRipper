"""Tests for `gui.blend_combiner.BlendCombinerPanel` set-backed _has_file."""

from __future__ import annotations

import pytest

from gui.blend_combiner import BlendCombinerPanel

pytestmark = pytest.mark.qt


def test_constructs(qtbot):
    w = BlendCombinerPanel()
    qtbot.addWidget(w)
    assert w._known_files == set()


def test_has_file_uses_set(qtbot):
    w = BlendCombinerPanel()
    qtbot.addWidget(w)
    w._file_list.addItem("/a/b/c.blend")
    w._known_files.add("/a/b/c.blend")
    assert w._has_file("/a/b/c.blend") is True
    assert w._has_file("/x/y.blend") is False


def test_clear_all_resets_set(qtbot):
    w = BlendCombinerPanel()
    qtbot.addWidget(w)
    w._file_list.addItem("/foo.blend")
    w._known_files.add("/foo.blend")
    w._clear_all()
    assert w._known_files == set()
    assert w._file_list.count() == 0


def test_remove_selected_drops_from_set(qtbot):
    w = BlendCombinerPanel()
    qtbot.addWidget(w)
    w._file_list.addItem("/a.blend")
    w._known_files.add("/a.blend")
    # Select all rows and remove
    w._file_list.selectAll()
    w._remove_selected()
    assert "/a.blend" not in w._known_files
    assert w._file_list.count() == 0
