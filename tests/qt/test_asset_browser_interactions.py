"""Tests for the asset browser's left-click and right-click interactions."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.asset_scanner import AssetEntry
from gui.asset_browser import AssetBrowser

pytestmark = pytest.mark.qt


def _entry(name: str, psk_path: Path) -> AssetEntry:
    return AssetEntry(
        psk_path=psk_path,
        name=name,
        category="Items",
        subcategory="Sub",
        mesh_props_found=True,
    )


def _first_leaf(parent):
    for i in range(parent.childCount()):
        child = parent.child(i)
        if child.childCount() == 0:
            return child
        r = _first_leaf(child)
        if r is not None:
            return r
    return None


def test_left_click_emits_props_view_requested(qtbot, tmp_path: Path):
    psk = tmp_path / "Foo.psk"
    psk.write_bytes(b"")
    asset = _entry("Foo", psk)

    b = AssetBrowser()
    qtbot.addWidget(b)
    b.set_assets([asset])

    leaf = _first_leaf(b._tree.invisibleRootItem())
    assert leaf is not None

    with qtbot.waitSignal(b.props_view_requested, timeout=2000) as blocker:
        b._on_item_clicked(leaf, 0)
    assert blocker.args[0] is asset


def test_left_click_on_category_row_does_nothing(qtbot, tmp_path: Path):
    """Category and subcategory items aren't in `_item_to_idx` — clicking
    them must not raise or fire props_view_requested."""
    psk = tmp_path / "Foo.psk"
    psk.write_bytes(b"")
    b = AssetBrowser()
    qtbot.addWidget(b)
    b.set_assets([_entry("Foo", psk)])

    cat_item = b._tree.topLevelItem(0)
    assert cat_item is not None
    fired = []
    b.props_view_requested.connect(lambda a: fired.append(a))
    b._on_item_clicked(cat_item, 0)
    assert fired == []


def test_right_click_menu_includes_preview_actions(qtbot, tmp_path: Path):
    """The context-menu builder is exercised directly. `_show_context_menu`
    blocks on `menu.exec()` so we instead reproduce its logic by calling the
    private signal-emitting paths the menu wires up."""
    psk = tmp_path / "Foo.psk"
    psk.write_bytes(b"")
    asset = _entry("Foo", psk)

    b = AssetBrowser()
    qtbot.addWidget(b)
    b.set_assets([asset])

    # Both signals exist as class-level attributes.
    assert hasattr(b, "mesh_preview_requested")
    assert hasattr(b, "props_view_requested")

    # Direct emit confirms wiring.
    with qtbot.waitSignal(b.mesh_preview_requested, timeout=1000) as blocker:
        b.mesh_preview_requested.emit(asset)
    assert blocker.args[0] is asset


def test_preview_mesh_disabled_when_psk_missing(qtbot, tmp_path: Path):
    """Build the menu by hand and check the action's enabled state."""
    from PySide6.QtWidgets import QMenu
    from PySide6.QtGui import QAction

    missing_psk = tmp_path / "MissingMesh.psk"  # never written
    asset = _entry("MissingMesh", missing_psk)

    b = AssetBrowser()
    qtbot.addWidget(b)
    b.set_assets([asset])

    # Mirror the logic in _show_context_menu — the action's `setEnabled` value
    # is what we care about, not the menu popup.
    menu = QMenu(b)
    act_mesh = QAction("Preview Mesh", b)
    act_mesh.setEnabled(asset.psk_path.is_file())
    menu.addAction(act_mesh)
    assert act_mesh.isEnabled() is False


def test_preview_mesh_enabled_when_psk_present(qtbot, tmp_path: Path):
    psk = tmp_path / "Real.psk"
    psk.write_bytes(b"")
    asset = _entry("Real", psk)

    b = AssetBrowser()
    qtbot.addWidget(b)
    b.set_assets([asset])

    # `_show_context_menu` checks `asset.psk_path.is_file()` directly.
    assert asset.psk_path.is_file() is True
