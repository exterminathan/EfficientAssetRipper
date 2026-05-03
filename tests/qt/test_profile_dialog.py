"""Tests for `gui.profile_dialog.ProfileDialog`.

Covers: list population, editor round-trip, Apply/Cancel semantics, and the
auto_save_paths checkbox round-tripping into the profile JSON.
"""

from __future__ import annotations

import pytest

from core.profile_manager import ProfileManager
from gui.profile_dialog import ProfileDialog

pytestmark = [pytest.mark.qt, pytest.mark.gui]


def test_profile_dialog_lists_existing_profiles(qtbot, tmp_profiles_dir):
    pm = ProfileManager()
    pm.create_profile("Alpha", {"game_dir": "A"})
    pm.create_profile("Beta", {"game_dir": "B"})

    dlg = ProfileDialog(pm, current_profile="Alpha")
    qtbot.addWidget(dlg)

    items = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    assert items == ["Alpha", "Beta"]
    # Editor pre-populated from selected profile
    assert dlg._editor._game_dir.text() == "A"


def test_apply_persists_edits_to_disk(qtbot, tmp_profiles_dir):
    pm = ProfileManager()
    pm.create_profile("Game", {"game_dir": "old"})

    dlg = ProfileDialog(pm, current_profile="Game")
    qtbot.addWidget(dlg)
    dlg._editor._game_dir.setText("new-path")
    dlg._editor._mounted_dir.setText("mounted")
    dlg._editor._output_dir.setText("output")
    dlg._editor._auto_save_chk.setChecked(True)
    dlg._on_apply()

    loaded = pm.load_profile("Game")
    assert loaded["game_dir"] == "new-path"
    assert loaded["unpack_output_dir"] == "mounted"
    assert loaded["blender_output_dir"] == "output"
    assert loaded["auto_save_paths"] is True


def test_cancel_discards_in_memory_edits(qtbot, tmp_profiles_dir):
    pm = ProfileManager()
    pm.create_profile("Game", {"game_dir": "original"})

    dlg = ProfileDialog(pm, current_profile="Game")
    qtbot.addWidget(dlg)
    dlg._editor._game_dir.setText("modified-but-not-applied")
    dlg._on_cancel()

    loaded = pm.load_profile("Game")
    assert loaded["game_dir"] == "original"


def test_ok_emits_profile_changed_with_active_name(qtbot, tmp_profiles_dir):
    pm = ProfileManager()
    pm.create_profile("Active", {})

    dlg = ProfileDialog(pm, current_profile="Active")
    qtbot.addWidget(dlg)

    with qtbot.waitSignal(dlg.profile_changed, timeout=1000) as sig:
        dlg._on_ok()
    assert sig.args == ["Active"]


def test_auto_save_paths_default_false(qtbot, tmp_profiles_dir):
    """A freshly created profile should have auto_save_paths defaulting to False."""
    pm = ProfileManager()
    pm.create_profile("Fresh", {})
    loaded = pm.load_profile("Fresh")
    assert loaded.get("auto_save_paths") is False


def test_editor_collects_aes_key_table(qtbot, tmp_profiles_dir):
    """AES keys typed into the table should round-trip through collect_data."""
    pm = ProfileManager()
    pm.create_profile("Game", {})

    dlg = ProfileDialog(pm, current_profile="Game")
    qtbot.addWidget(dlg)
    dlg._editor._add_key_row()
    # Default placeholder row needs a non-empty key to be collected
    from PySide6.QtWidgets import QTableWidgetItem
    dlg._editor._keys_table.setItem(0, 2, QTableWidgetItem("0xDEADBEEF"))

    data = dlg._editor.collect_data()
    assert any(k.get("key") == "0xDEADBEEF" for k in data["aes_keys"])
