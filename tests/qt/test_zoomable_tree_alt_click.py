"""Tests for Alt-click recursive expand/collapse on ZoomableTree."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QTreeWidgetItem

from gui.widgets import LAZY_PLACEHOLDER, ZoomableTree

pytestmark = [pytest.mark.qt, pytest.mark.gui]


def _make_tree(qtbot) -> ZoomableTree:
    tree = ZoomableTree()
    tree.setColumnCount(1)
    qtbot.addWidget(tree)
    return tree


def _build_three_levels(tree: ZoomableTree) -> tuple[QTreeWidgetItem, QTreeWidgetItem, QTreeWidgetItem]:
    root = QTreeWidgetItem(tree, ["root"])
    mid = QTreeWidgetItem(root, ["mid"])
    leaf = QTreeWidgetItem(mid, ["leaf"])
    return root, mid, leaf


def _click(tree: ZoomableTree, item: QTreeWidgetItem, modifiers: Qt.KeyboardModifier):
    rect = tree.visualItemRect(item)
    pos = rect.center() if not rect.isNull() else QPoint(2, 2)
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        modifiers,
    )
    tree.mousePressEvent(event)


def test_expand_recursive_opens_all_descendants(qtbot):
    tree = _make_tree(qtbot)
    root, mid, leaf = _build_three_levels(tree)
    assert root.isExpanded() is False
    assert mid.isExpanded() is False

    tree.expand_recursive(root)

    assert root.isExpanded() is True
    assert mid.isExpanded() is True


def test_collapse_recursive_closes_all_descendants(qtbot):
    tree = _make_tree(qtbot)
    root, mid, leaf = _build_three_levels(tree)
    tree.expand_recursive(root)
    assert mid.isExpanded() is True

    tree.collapse_recursive(root)

    assert root.isExpanded() is False
    assert mid.isExpanded() is False


def test_alt_click_on_collapsed_folder_expands_recursively(qtbot):
    tree = _make_tree(qtbot)
    root, mid, leaf = _build_three_levels(tree)
    # Tree must be visible for visualItemRect to be useful — but offscreen
    # Qt is configured in conftest, so showing is harmless and load-bearing
    # for hit-testing the item position.
    tree.show()  # required so visualItemRect returns a non-null rect for hit-testing
    qtbot.waitExposed(tree)

    _click(tree, root, Qt.KeyboardModifier.AltModifier)

    assert root.isExpanded() is True
    assert mid.isExpanded() is True


def test_alt_click_on_expanded_folder_collapses_recursively(qtbot):
    tree = _make_tree(qtbot)
    root, mid, leaf = _build_three_levels(tree)
    tree.expand_recursive(root)
    tree.show()  # required so visualItemRect can hit-test the item
    qtbot.waitExposed(tree)

    _click(tree, root, Qt.KeyboardModifier.AltModifier)

    assert root.isExpanded() is False
    assert mid.isExpanded() is False


def test_alt_click_on_leaf_is_noop(qtbot):
    tree = _make_tree(qtbot)
    leaf = QTreeWidgetItem(tree, ["only-leaf"])
    tree.show()  # required so visualItemRect can hit-test the item
    qtbot.waitExposed(tree)

    _click(tree, leaf, Qt.KeyboardModifier.AltModifier)

    # No children → no expansion state to change. The click should not raise.
    assert leaf.isExpanded() is False


def test_shift_alt_click_does_not_recurse_expand(qtbot):
    """Shift+Alt+click on a checkable item should still range-toggle, not recurse-expand."""
    tree = _make_tree(qtbot)
    a = QTreeWidgetItem(tree, ["a"])
    b = QTreeWidgetItem(tree, ["b"])
    QTreeWidgetItem(b, ["b-child"])  # makes 'b' expandable
    for it in (a, b):
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(0, Qt.CheckState.Unchecked)
    tree.show()  # required so visualItemRect can hit-test the items
    qtbot.waitExposed(tree)

    # Prime the last-clicked-item with a regular click on 'a'.
    _click(tree, a, Qt.KeyboardModifier.NoModifier)
    # Shift+Alt+click on 'b' — Shift wins and toggles the range, b should
    # NOT recursively expand.
    _click(tree, b, Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier)

    assert b.isExpanded() is False


def test_expand_recursive_stops_at_lazy_placeholder(qtbot):
    """A folder with only a placeholder child kicks one expand and stops."""
    tree = _make_tree(qtbot)
    folder = QTreeWidgetItem(tree, ["lazy"])
    placeholder = QTreeWidgetItem(folder, [""])
    placeholder.setData(0, Qt.ItemDataRole.UserRole, LAZY_PLACEHOLDER)

    fired: list[QTreeWidgetItem] = []
    tree.set_lazy_load_callback(lambda it: fired.append(it))

    tree.expand_recursive(folder)

    assert folder.isExpanded() is True
    assert fired == [folder]


def test_expand_all_visible_skips_hidden_rows(qtbot):
    tree = _make_tree(qtbot)
    visible = QTreeWidgetItem(tree, ["visible"])
    QTreeWidgetItem(visible, ["v-child"])
    hidden = QTreeWidgetItem(tree, ["hidden"])
    QTreeWidgetItem(hidden, ["h-child"])
    hidden.setHidden(True)

    tree.expand_all_visible()

    assert visible.isExpanded() is True
    assert hidden.isExpanded() is False
