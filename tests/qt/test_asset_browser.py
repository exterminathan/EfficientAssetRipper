"""Tests for `gui.asset_browser.AssetBrowser`."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.asset_scanner import AssetEntry
from gui.asset_browser import AssetBrowser

pytestmark = pytest.mark.qt


def _entry(name: str, cat: str, sub: str = "Sub") -> AssetEntry:
    return AssetEntry(
        psk_path=Path(rf"C:\Game\{cat}\{sub}\{name}.psk"),
        name=name,
        category=cat,
        subcategory=sub,
        mesh_props_found=True,
    )


def test_asset_browser_constructs_empty(qtbot):
    b = AssetBrowser()
    qtbot.addWidget(b)
    assert b.get_assets() == []


def test_set_assets_populates_tree(qtbot):
    b = AssetBrowser()
    qtbot.addWidget(b)
    b.set_assets([_entry("A", "Characters"), _entry("B", "Items")])
    assert len(b.get_assets()) == 2
    # Top-level items are categories
    assert b._tree.topLevelItemCount() >= 1


def test_category_filter_combobox_populated_from_data(qtbot):
    b = AssetBrowser()
    qtbot.addWidget(b)
    b.set_assets([
        _entry("A", "Characters"),
        _entry("B", "Characters"),
        _entry("C", "Items"),
    ])
    cats = [b._cat_filter.itemText(i) for i in range(b._cat_filter.count())]
    assert "All Categories" in cats
    # Items render as "Characters (N)" — match by prefix
    assert any(c.startswith("Characters") for c in cats)
    assert any(c.startswith("Items") for c in cats)


def _count_leaves(b: AssetBrowser) -> int:
    """Walk the tree and count leaf nodes (assets), regardless of check state."""
    count = 0

    def _walk(parent):
        nonlocal count
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child.childCount() > 0:
                _walk(child)
            else:
                count += 1

    _walk(b._tree.invisibleRootItem())
    return count


def test_filter_text_narrows_visible(qtbot):
    b = AssetBrowser()
    qtbot.addWidget(b)
    b.set_assets([
        _entry("AlphaThing", "Characters"),
        _entry("BetaThing", "Characters"),
    ])
    assert _count_leaves(b) == 2

    b._search.setText("AlphaThing")
    assert _count_leaves(b) == 1

    b._search.setText("")
    assert _count_leaves(b) == 2


def test_get_selected_assets_returns_only_checked(qtbot):
    from PySide6.QtCore import Qt

    b = AssetBrowser()
    qtbot.addWidget(b)
    b.set_assets([_entry("X", "Items"), _entry("Y", "Items")])

    # Initially nothing checked
    assert b.get_selected_assets() == []

    # Check the first leaf manually
    def _first_leaf(parent):
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child.childCount() == 0:
                return child
            r = _first_leaf(child)
            if r is not None:
                return r
        return None

    leaf = _first_leaf(b._tree.invisibleRootItem())
    assert leaf is not None
    leaf.setCheckState(0, Qt.CheckState.Checked)
    selected = b.get_selected_assets()
    assert len(selected) == 1
