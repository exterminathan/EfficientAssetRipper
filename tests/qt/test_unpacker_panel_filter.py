"""Tests for the Unpacker panel's search + asset-type filter behaviour."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidgetItem

from core import type_cache as tc
from core.type_cache import TypeCache
from gui.unpacker_panel import UnpackerPanel

pytestmark = pytest.mark.qt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_folder(parent, vfs_path: str, name: str) -> QTreeWidgetItem:
    item = QTreeWidgetItem(parent, [name])
    item.setData(0, Qt.ItemDataRole.UserRole, vfs_path)
    item.setData(0, Qt.ItemDataRole.UserRole + 1, True)
    return item


def _make_placeholder_folder(parent, vfs_path: str, name: str) -> QTreeWidgetItem:
    """Folder with a single placeholder child (lazy-loaded, not yet expanded)."""
    item = _make_folder(parent, vfs_path, name)
    ph = QTreeWidgetItem(item, ["Loading..."])
    ph.setData(0, Qt.ItemDataRole.UserRole, "__placeholder__")
    return item


def _make_file(parent, vfs_path: str, name: str | None = None) -> QTreeWidgetItem:
    display = name or vfs_path.rsplit("/", 1)[-1]
    item = QTreeWidgetItem(parent, [display])
    item.setData(0, Qt.ItemDataRole.UserRole, vfs_path)
    item.setData(0, Qt.ItemDataRole.UserRole + 1, False)
    return item


def _make_export(parent, package_path: str, name: str, export_type: str) -> QTreeWidgetItem:
    item = QTreeWidgetItem(parent, [f"{name}  [{export_type}]"])
    item.setData(0, Qt.ItemDataRole.UserRole, package_path)
    item.setData(0, Qt.ItemDataRole.UserRole + 1, False)
    item.setData(0, Qt.ItemDataRole.UserRole + 4, export_type)
    return item


def _new_panel(qtbot) -> UnpackerPanel:
    p = UnpackerPanel()
    qtbot.addWidget(p)
    return p


def _visible_descendants(item: QTreeWidgetItem) -> list[str]:
    """Return display text of every visible descendant, depth-first."""
    out: list[str] = []
    for i in range(item.childCount()):
        ch = item.child(i)
        if not ch.isHidden():
            out.append(ch.text(0))
            out.extend(_visible_descendants(ch))
    return out


# ---------------------------------------------------------------------------
# _row_categories
# ---------------------------------------------------------------------------

def test_row_categories_uses_export_type_when_set(qtbot):
    p = _new_panel(qtbot)
    item = _make_export(p._tree, "/Game/X.uasset", "T_Foo", "Texture2D")
    assert p._row_categories(item) == {tc.CATEGORY_TEXTURE}


def test_row_categories_uses_cache_for_unexpanded_package(qtbot):
    p = _new_panel(qtbot)
    p._type_cache = TypeCache()
    p._type_cache.add_batch([
        {"path": "/Game/Mesh.uasset", "exports": [{"name": "SK", "export_type": "SkeletalMesh"}]},
    ])
    item = _make_file(p._tree, "/Game/Mesh.uasset")
    assert p._row_categories(item) == {tc.CATEGORY_MESH}


def test_row_categories_falls_back_to_extension(qtbot):
    p = _new_panel(qtbot)
    psk = _make_file(p._tree, "/Game/M.psk")
    png = _make_file(p._tree, "/Game/T.png")
    wav = _make_file(p._tree, "/Game/A.wav")
    other = _make_file(p._tree, "/Game/F.txt")
    assert p._row_categories(psk) == {tc.CATEGORY_MESH}
    assert p._row_categories(png) == {tc.CATEGORY_TEXTURE}
    assert p._row_categories(wav) == {tc.CATEGORY_AUDIO}
    assert p._row_categories(other) == {tc.CATEGORY_OTHER}


def test_row_categories_unscanned_package_during_scan_is_other(qtbot):
    p = _new_panel(qtbot)
    p._type_cache = TypeCache()  # empty
    p._type_scan_in_progress = True
    item = _make_file(p._tree, "/Game/Anything.uasset")
    # Uncached packages default to Other so category filters stay meaningful.
    assert p._row_categories(item) == {tc.CATEGORY_OTHER}


def test_row_categories_wwise_virtual_audio(qtbot):
    p = _new_panel(qtbot)
    item = _make_file(p._tree, "/G/foo.wem")
    item.setData(0, Qt.ItemDataRole.UserRole + 2, {"debug_name": "foo"})
    assert p._row_categories(item) == {tc.CATEGORY_AUDIO}


# ---------------------------------------------------------------------------
# Search by name
# ---------------------------------------------------------------------------

def test_search_hides_non_matching_files_and_keeps_ancestors(qtbot):
    p = _new_panel(qtbot)
    root_folder = _make_folder(p._tree.invisibleRootItem(), "/G/Foo", "Foo")
    _make_file(root_folder, "/G/Foo/Hair_TX.uasset")
    _make_file(root_folder, "/G/Foo/Body.uasset")

    p._search.setText("hair")
    p._filter_tree()

    visible = _visible_descendants(p._tree.invisibleRootItem())
    assert "Foo" in visible
    assert "Hair_TX.uasset" in visible
    assert "Body.uasset" not in visible
    # Auto-expand revealed the folder
    assert root_folder.isExpanded()


def test_search_strips_bracket_type_suffix_from_match(qtbot):
    """Searching '_TX' must match base names, not the [Type] label."""
    p = _new_panel(qtbot)
    parent = _make_folder(p._tree.invisibleRootItem(), "/G", "G")
    # Two exports — only the first has "_TX" in its base name. Both are Texture2D
    # so without the strip, both would match because "[Texture2D]" contains "tx".
    _make_export(parent, "/G/A.uasset", "Foo_TX", "Texture2D")
    _make_export(parent, "/G/B.uasset", "Plain", "Texture2D")

    p._search.setText("_tx")
    p._filter_tree()

    visible = _visible_descendants(p._tree.invisibleRootItem())
    assert any("Foo_TX" in v for v in visible)
    assert not any("Plain  [Texture2D]" == v for v in visible)


def test_search_clear_restores_auto_expanded_folders(qtbot):
    p = _new_panel(qtbot)
    folder = _make_folder(p._tree.invisibleRootItem(), "/G", "G")
    _make_file(folder, "/G/Hair.uasset")
    assert not folder.isExpanded()

    p._search.setText("hair")
    p._filter_tree()
    assert folder.isExpanded()
    assert folder in p._auto_expanded

    p._search.setText("")
    p._filter_tree()
    assert not folder.isExpanded()
    assert folder not in p._auto_expanded


def test_search_clear_does_not_collapse_user_expanded_folders(qtbot):
    p = _new_panel(qtbot)
    folder = _make_folder(p._tree.invisibleRootItem(), "/G", "G")
    _make_file(folder, "/G/A.uasset")

    # User manually expands first.
    folder.setExpanded(True)
    # Filter sees it's already expanded — does NOT add to _auto_expanded.
    p._search.setText("a")
    p._filter_tree()
    assert folder not in p._auto_expanded

    # Clearing must leave the user's expansion alone.
    p._search.setText("")
    p._filter_tree()
    assert folder.isExpanded()


# ---------------------------------------------------------------------------
# Category filter
# ---------------------------------------------------------------------------

def test_category_filter_only_textures_hides_meshes(qtbot):
    p = _new_panel(qtbot)
    folder = _make_folder(p._tree.invisibleRootItem(), "/G", "G")
    tex = _make_export(folder, "/G/T.uasset", "T", "Texture2D")
    mesh = _make_export(folder, "/G/M.uasset", "M", "SkeletalMesh")

    # Uncheck everything except Textures.
    for cat_id, cb in p._cat_checkboxes.items():
        cb.setChecked(cat_id == tc.CATEGORY_TEXTURE)
    p._filter_tree()

    assert not tex.isHidden()
    assert mesh.isHidden()


def test_category_filter_uses_cache_for_unexpanded_packages(qtbot):
    p = _new_panel(qtbot)
    p._type_cache = TypeCache()
    p._type_cache.add_batch([
        {"path": "/G/Tex.uasset", "exports": [{"name": "T", "export_type": "Texture2D"}]},
        {"path": "/G/Mesh.uasset", "exports": [{"name": "M", "export_type": "SkeletalMesh"}]},
    ])
    folder = _make_folder(p._tree.invisibleRootItem(), "/G", "G")
    tex_pkg = _make_file(folder, "/G/Tex.uasset")
    mesh_pkg = _make_file(folder, "/G/Mesh.uasset")

    for cat_id, cb in p._cat_checkboxes.items():
        cb.setChecked(cat_id == tc.CATEGORY_TEXTURE)
    p._filter_tree()

    assert not tex_pkg.isHidden()
    assert mesh_pkg.isHidden()


# ---------------------------------------------------------------------------
# type_contains
# ---------------------------------------------------------------------------

def test_type_contains_substring_filter(qtbot):
    p = _new_panel(qtbot)
    folder = _make_folder(p._tree.invisibleRootItem(), "/G", "G")
    anim = _make_export(folder, "/G/A.uasset", "Walk", "AnimSequence")
    mesh = _make_export(folder, "/G/M.uasset", "Hero", "SkeletalMesh")

    p._type_contains.setText("anim")
    p._filter_tree()

    assert not anim.isHidden()
    assert mesh.isHidden()


# ---------------------------------------------------------------------------
# Combined axes
# ---------------------------------------------------------------------------

def test_search_and_category_combined_must_pass_both(qtbot):
    p = _new_panel(qtbot)
    folder = _make_folder(p._tree.invisibleRootItem(), "/G", "G")
    hair_mesh = _make_export(folder, "/G/A.uasset", "Hair_Mesh", "SkeletalMesh")
    hair_tex = _make_export(folder, "/G/B.uasset", "Hair_TX", "Texture2D")

    p._search.setText("hair")
    for cat_id, cb in p._cat_checkboxes.items():
        cb.setChecked(cat_id == tc.CATEGORY_MESH)
    p._filter_tree()

    assert not hair_mesh.isHidden()
    assert hair_tex.isHidden()


def test_folder_name_match_passes_axis_to_descendants(qtbot):
    """If the folder name matches, descendants get a free pass on the name axis
    but must still pass the type axis."""
    p = _new_panel(qtbot)
    hair_folder = _make_folder(p._tree.invisibleRootItem(), "/G/Hair", "Hair")
    sk = _make_export(hair_folder, "/G/Hair/A.uasset", "FooSK", "SkeletalMesh")
    tx = _make_export(hair_folder, "/G/Hair/B.uasset", "FooTX", "Texture2D")

    p._search.setText("hair")
    for cat_id, cb in p._cat_checkboxes.items():
        cb.setChecked(cat_id == tc.CATEGORY_MESH)
    p._filter_tree()

    assert not sk.isHidden()
    assert tx.isHidden()
    assert not hair_folder.isHidden()


# ---------------------------------------------------------------------------
# Placeholder folder visibility via folder index
# ---------------------------------------------------------------------------

def test_placeholder_folder_hidden_when_category_has_no_match(qtbot):
    p = _new_panel(qtbot)
    p._type_cache = TypeCache()
    p._type_cache.add_batch([
        {"path": "G/Textures/T_Foo.uasset",
         "exports": [{"name": "T_Foo", "export_type": "Texture2D"}]},
    ])
    p._type_cache.rebuild_folder_index()

    folder = _make_placeholder_folder(p._tree.invisibleRootItem(), "G", "G")

    for cat_id, cb in p._cat_checkboxes.items():
        cb.setChecked(cat_id == tc.CATEGORY_MESH)
    p._filter_tree()

    assert folder.isHidden()


def test_placeholder_folder_shown_when_category_matches(qtbot):
    p = _new_panel(qtbot)
    p._type_cache = TypeCache()
    p._type_cache.add_batch([
        {"path": "G/Meshes/SK_Foo.uasset",
         "exports": [{"name": "SK_Foo", "export_type": "SkeletalMesh"}]},
    ])
    p._type_cache.rebuild_folder_index()

    folder = _make_placeholder_folder(p._tree.invisibleRootItem(), "G", "G")

    for cat_id, cb in p._cat_checkboxes.items():
        cb.setChecked(cat_id == tc.CATEGORY_MESH)
    p._filter_tree()

    assert not folder.isHidden()


def test_placeholder_folder_stays_visible_when_not_in_index(qtbot):
    """Folders with no indexed data are not hidden — can't hide what we don't know."""
    p = _new_panel(qtbot)
    p._type_cache = TypeCache()
    p._type_cache.rebuild_folder_index()

    folder = _make_placeholder_folder(p._tree.invisibleRootItem(), "Unknown/Folder", "Folder")

    for cat_id, cb in p._cat_checkboxes.items():
        cb.setChecked(cat_id == tc.CATEGORY_MESH)
    p._filter_tree()

    assert not folder.isHidden()


def test_no_filter_active_shows_everything(qtbot):
    p = _new_panel(qtbot)
    folder = _make_folder(p._tree.invisibleRootItem(), "/G", "G")
    a = _make_file(folder, "/G/A.uasset")
    b = _make_export(folder, "/G/B.uasset", "X", "AnimSequence")

    p._filter_tree()  # all defaults — no filter active
    assert not folder.isHidden()
    assert not a.isHidden()
    assert not b.isHidden()
