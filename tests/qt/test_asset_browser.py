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
    # Filter rebuilds are debounced (150 ms) — wait for the timer to fire
    # rather than racing it.
    qtbot.waitUntil(lambda: _count_leaves(b) == 1, timeout=2000)

    b._search.setText("")
    qtbot.waitUntil(lambda: _count_leaves(b) == 2, timeout=2000)


def test_filter_debounce_coalesces_rapid_typing(qtbot):
    """Rapid textChanged events should produce a single rebuild after the timer."""
    b = AssetBrowser()
    qtbot.addWidget(b)
    b.set_assets([
        _entry("AlphaThing", "Characters"),
        _entry("BetaThing", "Characters"),
    ])
    rebuilds = []
    original = b._rebuild_tree

    def _spy():
        rebuilds.append(True)
        original()

    b._rebuild_tree = _spy
    b._filter_debounce.timeout.disconnect()
    b._filter_debounce.timeout.connect(_spy)

    for ch in ("A", "Al", "Alp"):
        b._search.setText(ch)
    qtbot.waitUntil(lambda: len(rebuilds) >= 1, timeout=2000)
    # All three keystrokes should have collapsed into one rebuild.
    assert len(rebuilds) == 1


def test_assets_property_returns_list(qtbot):
    b = AssetBrowser()
    qtbot.addWidget(b)
    e = _entry("X", "Items")
    b.set_assets([e])
    assert b.assets == [e]


def test_asset_detail_dialog_warns_when_uncategorized(qtbot):
    """The classifier-failure surface in the detail dialog tells the user
    the path is not under the configured Game Folder."""
    from gui.asset_browser import AssetDetailDialog

    asset = AssetEntry(
        psk_path=Path(r"F:\Foo\Obduction\Content\Mesh\X.pskx"),
        name="X",
        category="Uncategorized",
        subcategory="Unknown",
        mesh_props_found=True,
    )
    dlg = AssetDetailDialog(asset)
    qtbot.addWidget(dlg)

    # Find the Category QLabel — it carries the warning text now.
    from PySide6.QtWidgets import QLabel
    labels = [c for c in dlg.findChildren(QLabel)]
    cat_text = " ".join(lbl.text() for lbl in labels)
    assert "Uncategorized" in cat_text
    assert "not under" in cat_text.lower()


def test_asset_detail_dialog_marks_keyword_fallback_textures(qtbot):
    """Textures filled by keyword fallback should be visually flagged."""
    from gui.asset_browser import AssetDetailDialog
    from core.asset_scanner import MaterialEntry
    from core.texture_resolver import ResolvedTexture

    mat = MaterialEntry(
        slot_name="Slot",
        material_name="MI_Mat",
        textures=[
            ResolvedTexture(
                slot="base_color",
                texture_name="T_Guess",
                path=Path(r"C:\Game\Textures\T_Guess.tga"),
                colorspace="sRGB",
                wiring={"type": "direct", "target_input": "Base Color"},
            )
        ],
        keyword_fallback_used=["base_color"],
    )
    asset = AssetEntry(
        psk_path=Path(r"C:\Game\Mesh\X.psk"),
        name="X",
        category="Other",
        subcategory="General",
        materials=[mat],
        mesh_props_found=True,
    )
    dlg = AssetDetailDialog(asset)
    qtbot.addWidget(dlg)
    from PySide6.QtWidgets import QLabel
    text = " ".join(lbl.text() for lbl in dlg.findChildren(QLabel))
    assert "auto-detected" in text


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
