"""Tests for the unpacker panel's type-aware right-click preview menu and
the temp-export-for-preview dispatch in `_on_export_done`."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QMenu, QTreeWidgetItem

from gui.unpacker_panel import UnpackerPanel

pytestmark = pytest.mark.qt


def _capture_menu_actions(panel: UnpackerPanel, item: QTreeWidgetItem) -> list[str]:
    """Drive `_popup_context_menu` and return the list of action labels.

    Patches `QMenu.__init__` so the *next* QMenu created has its `exec`
    replaced by a recorder. This is the only reliable way to introspect
    a QMenu built and shown inside a single Python call — class-level
    method patches don't take on the C++-backed exec slot.
    """
    captured: list[list[str]] = []
    orig_init = QMenu.__init__

    def hooked(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        def _rec(*a, **k):
            captured.append([action.text() for action in self.actions()])
            return None
        self.exec = _rec  # type: ignore[assignment]

    QMenu.__init__ = hooked  # type: ignore[assignment]
    try:
        panel._popup_context_menu(item, QPoint(0, 0))
    finally:
        QMenu.__init__ = orig_init  # type: ignore[assignment]
    return captured[0] if captured else []


# ---------------------------------------------------------------------------
# _classify_row
# ---------------------------------------------------------------------------

def _make_row(panel: UnpackerPanel, vfs_path: str, *,
              is_folder: bool = False,
              audio_data: dict | None = None,
              export_type: str | None = None) -> QTreeWidgetItem:
    """Build a tree item with the same UserRole layout as the real panel."""
    item = QTreeWidgetItem(panel._tree)
    item.setData(0, Qt.ItemDataRole.UserRole, vfs_path)
    item.setData(0, Qt.ItemDataRole.UserRole + 1, is_folder)
    if audio_data is not None:
        item.setData(0, Qt.ItemDataRole.UserRole + 2, audio_data)
    if export_type is not None:
        item.setData(0, Qt.ItemDataRole.UserRole + 4, export_type)
    return item


def test_classify_row_export_type_mesh(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    for et in ("SkeletalMesh", "StaticMesh"):
        item = _make_row(p, "/Game/Foo.uasset", export_type=et)
        assert p._classify_row(item) == "mesh"


def test_classify_row_export_type_texture(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    for et in ("Texture2D", "TextureCube", "Texture2DArray"):
        item = _make_row(p, "/Game/Foo.uasset", export_type=et)
        assert p._classify_row(item) == "texture"


def test_classify_row_export_type_audio(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    for et in ("SoundCue", "SoundWave", "AkAudioEvent"):
        item = _make_row(p, "/Game/Foo.uasset", export_type=et)
        assert p._classify_row(item) == "audio"


def test_classify_row_unknown_export_type(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    item = _make_row(p, "/Game/Foo.uasset", export_type="Material")
    assert p._classify_row(item) == "unknown"


def test_classify_row_wwise_audio_data_overrides_extension(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    item = _make_row(
        p, "/Game/Foo.uasset",  # not an audio extension
        audio_data={"wem_vfs_path": "/x", "debug_name": "x"},
    )
    assert p._classify_row(item) == "audio"


def test_classify_row_extension_fallbacks(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    cases = [
        ("/Game/T.png", "texture"),
        ("/Game/T.tga", "texture"),
        ("/Game/T.dds", "texture"),
        ("/Game/M.psk", "mesh"),
        ("/Game/M.pskx", "mesh"),
        ("/Game/A.wav", "audio"),
        ("/Game/A.wem", "audio"),
        # Unexpanded packages classify as "package" — the menu offers all
        # three preview kinds for them since we don't know the contents.
        ("/Game/X.uasset", "package"),
        ("/Game/X.upk", "package"),
        ("/Game/X.umap", "package"),
        ("/Game/X.weird", "unknown"),
    ]
    for vfs, expected in cases:
        item = _make_row(p, vfs)
        assert p._classify_row(item) == expected, f"{vfs} → {expected}"


# ---------------------------------------------------------------------------
# _on_export_done dispatch (temp-export-for-preview)
# ---------------------------------------------------------------------------

def test_on_export_done_dispatches_mesh_kind(qtbot, tmp_path: Path):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    p._mesh_preview_temp_dir = tmp_path
    psk = tmp_path / "Game" / "Foo.psk"
    psk.parent.mkdir(parents=True, exist_ok=True)
    psk.write_bytes(b"")
    p._pending_temp_preview = (str(psk), "mesh", "/Game/Foo.uasset")
    p._exporting = True

    with qtbot.waitSignal(p.mesh_preview, timeout=2000) as blocker:
        p._on_export_done([str(psk)], [])
    assert blocker.args[0] == str(psk)
    assert p._pending_temp_preview is None
    assert p._exporting is False


def test_on_export_done_dispatches_texture_kind(qtbot, tmp_path: Path):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    p._tga_preview_temp_dir = tmp_path
    png = tmp_path / "Game" / "Foo.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b"")
    p._pending_temp_preview = (str(png), "texture", "/Game/Foo.uasset")
    p._exporting = True

    with qtbot.waitSignal(p.tga_preview, timeout=2000) as blocker:
        p._on_export_done([str(png)], [])
    assert blocker.args[0] == str(png)


def test_on_export_done_dispatches_audio_kind(qtbot, tmp_path: Path):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    p._audio_preview_temp_dir = tmp_path
    wav = tmp_path / "Game" / "Foo.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_bytes(b"")
    p._pending_temp_preview = (str(wav), "audio", "/Game/Foo.uasset")
    p._exporting = True

    with qtbot.waitSignal(p.audio_preview, timeout=2000) as blocker:
        p._on_export_done([str(wav)], [])
    assert blocker.args[0] == str(wav)


def test_on_export_done_extension_swap_fallback(qtbot, tmp_path: Path):
    """CLI may write a different extension than predicted (e.g. .tga not .png).
    The dispatcher should pick from `succeeded` matching the kind's extensions."""
    p = UnpackerPanel()
    qtbot.addWidget(p)
    p._tga_preview_temp_dir = tmp_path
    actual = tmp_path / "Game" / "Foo.tga"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_bytes(b"")
    predicted = tmp_path / "Game" / "Foo.png"  # never created
    p._pending_temp_preview = (str(predicted), "texture", "/Game/Foo.uasset")
    p._exporting = True

    with qtbot.waitSignal(p.tga_preview, timeout=2000) as blocker:
        p._on_export_done([str(actual)], [])
    assert blocker.args[0] == str(actual)


# ---------------------------------------------------------------------------
# _kick_temp_export
# ---------------------------------------------------------------------------

def test_kick_temp_export_builds_correct_formats_dict(qtbot, tmp_path: Path, monkeypatch):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    p._mesh_preview_temp_dir = tmp_path
    p._mounted = True

    captured: dict = {}

    def fake_export(paths, output_dir, formats=None, texture_format="png", audio_format="wav"):
        captured.update({
            "paths": paths,
            "output_dir": output_dir,
            "formats": formats,
        })

    monkeypatch.setattr(p._unpacker, "export", fake_export)

    p._kick_temp_export("/Game/Bar.uasset", "mesh")

    assert captured["paths"] == ["/Game/Bar.uasset"]
    assert captured["output_dir"] == str(tmp_path)
    assert captured["formats"] == {
        "mesh": True, "texture": False, "props": False,
        "animation": False, "audio": False,
    }
    assert p._pending_temp_preview is not None
    expected_path, kind, vfs_path = p._pending_temp_preview
    assert kind == "mesh"
    assert Path(expected_path).suffix == ".psk"
    assert vfs_path == "/Game/Bar.uasset"


def test_kick_temp_export_refuses_when_not_mounted(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    p._mesh_preview_temp_dir = Path(".")
    p._mounted = False
    p._kick_temp_export("/Game/Foo.uasset", "mesh")
    assert p._pending_temp_preview is None
    assert "Mount" in p._status_label.text()


# ---------------------------------------------------------------------------
# Context menu construction
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Menu rendering — what's actually in the popup
# ---------------------------------------------------------------------------

def test_menu_for_unexpanded_uasset_offers_all_preview_kinds(qtbot):
    """An unexpanded .uasset could be a mesh, texture, or audio asset; the
    menu must offer all three so the user doesn't have to expand first."""
    p = UnpackerPanel()
    qtbot.addWidget(p)
    item = _make_row(p, "/Game/Foo.uasset")
    actions = _capture_menu_actions(p, item)
    assert actions == ["Preview Mesh", "Preview Texture", "Preview Audio", "Preview Properties"]


def test_menu_for_expanded_skeletal_mesh(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    item = _make_row(p, "/Game/Foo.uasset", export_type="SkeletalMesh")
    actions = _capture_menu_actions(p, item)
    assert actions == ["Preview Mesh", "Preview Properties"]


def test_menu_for_expanded_texture2d(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    item = _make_row(p, "/Game/Foo.uasset", export_type="Texture2D")
    actions = _capture_menu_actions(p, item)
    assert actions == ["Preview Texture", "Preview Properties"]


def test_menu_for_expanded_unknown_export_type_shows_only_props(qtbot):
    """An export type we don't know how to preview (e.g. Material) still
    shows Preview Properties so the user can inspect it."""
    p = UnpackerPanel()
    qtbot.addWidget(p)
    item = _make_row(p, "/Game/Foo.uasset", export_type="Material")
    actions = _capture_menu_actions(p, item)
    assert actions == ["Preview Properties"]


def test_menu_for_loose_psk_file(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    item = _make_row(p, "/Game/Mesh.psk")
    actions = _capture_menu_actions(p, item)
    assert actions == ["Preview Mesh", "Preview Properties"]


def test_menu_for_loose_image_file(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    item = _make_row(p, "/Game/Tex.png")
    actions = _capture_menu_actions(p, item)
    assert actions == ["Preview Texture", "Preview Properties"]


def test_menu_for_wwise_virtual_audio(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    item = _make_row(p, "/Game/audio.wem",
                     audio_data={"wem_vfs_path": "/x", "debug_name": "x"})
    actions = _capture_menu_actions(p, item)
    assert actions == ["Preview Audio", "Preview Properties"]


def test_menu_skipped_for_folders(qtbot):
    """Folder rows produce no menu at all — the early return prevents any
    QMenu from being constructed."""
    p = UnpackerPanel()
    qtbot.addWidget(p)
    item = _make_row(p, "/Game/Folder", is_folder=True)
    actions = _capture_menu_actions(p, item)
    assert actions == []


# ---------------------------------------------------------------------------
# Expanded .uasset menu — filter buttons by child types
# ---------------------------------------------------------------------------

def _make_package_with_children(panel: UnpackerPanel, vfs_path: str,
                                 child_export_types: list[str]) -> QTreeWidgetItem:
    """Build a .uasset package item with real children carrying export_type.
    Mirrors how `_on_exports_listed` populates the tree post-list_exports."""
    parent = _make_row(panel, vfs_path)
    for et in child_export_types:
        child = QTreeWidgetItem(parent)
        child.setData(0, Qt.ItemDataRole.UserRole, vfs_path)
        child.setData(0, Qt.ItemDataRole.UserRole + 1, False)
        child.setData(0, Qt.ItemDataRole.UserRole + 4, et)
    return parent


def test_menu_for_expanded_uasset_filters_by_mesh_child(qtbot):
    """An expanded mesh .uasset (StaticMesh + Material children) should offer
    only Preview Mesh — not Texture/Audio."""
    p = UnpackerPanel()
    qtbot.addWidget(p)
    item = _make_package_with_children(
        p, "/Game/SM_Splitter_01.uasset",
        ["StaticMesh", "Material"],  # Material is non-previewable
    )
    actions = _capture_menu_actions(p, item)
    assert actions == ["Preview Mesh", "Preview Properties"]


def test_menu_for_expanded_uasset_filters_by_texture_child(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    item = _make_package_with_children(
        p, "/Game/TX_Conveyor_AO.uasset", ["Texture2D"],
    )
    actions = _capture_menu_actions(p, item)
    assert actions == ["Preview Texture", "Preview Properties"]


def test_menu_for_expanded_uasset_no_previewable_children(qtbot):
    """A .uasset whose children are all non-previewable types still shows
    Preview Properties only — no inert preview buttons."""
    p = UnpackerPanel()
    qtbot.addWidget(p)
    item = _make_package_with_children(
        p, "/Game/MI_Foo.uasset", ["Material", "MaterialInstanceConstant"],
    )
    actions = _capture_menu_actions(p, item)
    assert actions == ["Preview Properties"]


# ---------------------------------------------------------------------------
# _on_export_done — flat-layout rescue (regression for "file not found" bug)
# ---------------------------------------------------------------------------

def test_on_export_done_finds_mesh_via_flat_rescue(qtbot, tmp_path: Path):
    """The CLI writes mesh exports flat (e.g. <temp>/MyMesh.psk) but
    `_predict_temp_output` builds a nested VFS-mirrored path and `succeeded`
    echoes the VFS input path, not the disk output. The rescan fallback via
    `_find_in_temp` should still locate the file on the *first* attempt."""
    p = UnpackerPanel()
    qtbot.addWidget(p)
    p._mesh_preview_temp_dir = tmp_path

    # Predicted (nested) path — doesn't exist on disk.
    predicted = tmp_path / "Game" / "Meshes" / "MyMesh.psk"
    # Actual (flat) path the CLI wrote.
    actual = tmp_path / "MyMesh.psk"
    actual.write_bytes(b"")

    p._pending_temp_preview = (str(predicted), "mesh", "/Game/Meshes/MyMesh.uasset")
    p._exporting = True

    # `succeeded` contains the VFS input path, not the disk output — exactly
    # what the CLI returns today and the source of the original bug.
    with qtbot.waitSignal(p.mesh_preview, timeout=2000) as blocker:
        p._on_export_done(["/Game/Meshes/MyMesh.uasset"], [])
    assert blocker.args[0] == str(actual)
    assert p._status_label.text() == "Ready"
