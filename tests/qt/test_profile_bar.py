"""Tests for `gui.profile_bar.ProfileBar`."""

from __future__ import annotations

import pytest

from core.profile_manager import ProfileManager
from gui.profile_bar import ProfileBar

pytestmark = pytest.mark.qt


def test_profile_bar_constructs_empty(qtbot, tmp_profiles_dir):
    pm = ProfileManager()
    bar = ProfileBar(pm)
    qtbot.addWidget(bar)
    bar.refresh()
    assert bar._combo.count() == 0


def test_profile_bar_refresh_lists_profiles(qtbot, tmp_profiles_dir):
    pm = ProfileManager()
    pm.create_profile("Alpha", {})
    pm.create_profile("Beta", {})
    bar = ProfileBar(pm)
    qtbot.addWidget(bar)
    bar.refresh()
    assert bar._combo.count() == 2
    items = [bar._combo.itemText(i) for i in range(bar._combo.count())]
    assert items == sorted(items)


def test_profile_bar_select_emits_switch_requested(qtbot, tmp_profiles_dir):
    pm = ProfileManager()
    pm.create_profile("Alpha", {})
    pm.create_profile("Beta", {})
    bar = ProfileBar(pm)
    qtbot.addWidget(bar)
    bar.refresh(select="Alpha")

    with qtbot.waitSignal(bar.profile_switch_requested, timeout=1000) as sig:
        # Find the index for "Beta" and call _on_combo_activated directly,
        # bypassing the modal busy-check dialog.
        idx = bar._combo.findText("Beta")
        bar._combo.setCurrentIndex(idx)
        bar._on_combo_activated(idx)
    assert sig.args == ["Beta"]


def test_set_current_updates_combo(qtbot, tmp_profiles_dir):
    pm = ProfileManager()
    pm.create_profile("X", {})
    pm.create_profile("Y", {})
    bar = ProfileBar(pm)
    qtbot.addWidget(bar)
    bar.refresh()
    bar.set_current("Y")
    assert bar.current_profile() == "Y"
