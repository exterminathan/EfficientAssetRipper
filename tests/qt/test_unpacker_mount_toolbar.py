"""Tests for the Unpacker's read-only Mount toolbar.

The toolbar replaces the old Mount Archives QGroupBox + duplicate AES table.
Game folder / UE version / mappings are display-only — editing happens in
Manage Profiles. AES keys are snapshotted from the active profile.
"""

from __future__ import annotations

import pytest

from gui.unpacker_panel import UnpackerPanel

pytestmark = [pytest.mark.qt, pytest.mark.gui]


@pytest.fixture
def panel(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    return p


def test_game_folder_field_is_readonly(panel):
    assert panel._game_dir_edit.isReadOnly() is True


def test_ue_version_combo_is_disabled(panel):
    assert panel._ue_version_combo.isEnabled() is False


def test_mappings_field_is_hidden(panel):
    """Mappings path is sourced from the profile and never shown in the toolbar."""
    assert panel._mappings_edit.isVisible() is False


def test_mount_button_is_enabled(panel):
    """Even with read-only displays, the Mount button must remain clickable."""
    assert panel._mount_btn.isEnabled() is True


def test_panel_has_no_local_aes_keys_table(panel):
    """The duplicated AES editor was removed — keys live in Manage Profiles."""
    assert not hasattr(panel, "_keys_table")
    assert not hasattr(panel, "_save_keys_to_config")
    assert not hasattr(panel, "_add_key_row")


def test_load_from_profile_snapshots_aes_keys(panel):
    profile = {
        "game_dir": "/some/game",
        "ue_version": "GAME_UE5_4",
        "mappings_path": "",
        "unpack_output_dir": "",
        "aes_keys": [
            {"label": "Main", "guid": "AA" * 16, "key": "0xDEADBEEF"},
            {"label": "Alt", "guid": "BB" * 16, "key": "0xCAFEBABE"},
        ],
    }
    panel.load_from_profile(profile)
    assert panel._get_aes_keys() == profile["aes_keys"]


def test_collect_for_profile_round_trips_aes_keys(panel):
    profile = {
        "game_dir": "",
        "ue_version": "GAME_UE5_4",
        "mappings_path": "",
        "unpack_output_dir": "",
        "aes_keys": [{"label": "X", "guid": "AA" * 16, "key": "0xFEEDFACE"}],
    }
    panel.load_from_profile(profile)
    collected = panel.collect_for_profile()
    assert collected["aes_keys"] == profile["aes_keys"]


def test_apply_profile_aes_keys_replaces_snapshot(panel):
    """The AES prompt path calls apply_profile_aes_keys after writing back to disk."""
    panel.apply_profile_aes_keys([{"label": "New", "guid": "00" * 16, "key": "DEADBEEF"}])
    keys = panel._get_aes_keys()
    assert len(keys) == 1
    assert keys[0]["key"] == "DEADBEEF"


def test_export_section_starts_collapsed(panel):
    """Export controls live in the collapsible footer; default closed."""
    assert hasattr(panel, "_export_section")
    assert panel._export_section._expanded is False


def test_aes_keys_required_signal_emits_on_unmounted(panel, qtbot):
    """When init_done reports unmounted_count>0, the panel surfaces the prompt."""
    with qtbot.waitSignal(panel.aes_keys_required, timeout=1000) as sig:
        panel._on_initialized(
            archive_count=2,
            unmounted_count=1,
            file_count=10,
            keys_submitted=0,
            loose_file_count=0,
            unmounted_archives=[{"name": "encrypted.pak", "guid": "AA" * 16}],
        )
    assert sig.args[0] == 1
    assert sig.args[1] == [{"name": "encrypted.pak", "guid": "AA" * 16}]


def test_aes_keys_required_not_re_emitted_on_remount_loop(panel):
    """Guard against re-prompting when a remount triggered by the prompt
    itself still reports unmounted archives (wrong key)."""
    fired: list[tuple[int, list]] = []
    panel.aes_keys_required.connect(lambda c, a: fired.append((c, a)))

    panel._on_initialized(2, 1, 10, 0, 0, [{"name": "x.pak", "guid": "AA" * 16}])
    assert len(fired) == 1
    # Simulate the second remount cycle following accept-with-wrong-key.
    panel._on_initialized(2, 1, 10, 1, 0, [{"name": "x.pak", "guid": "AA" * 16}])
    assert len(fired) == 1, "expected the second unmounted init to be silent"
