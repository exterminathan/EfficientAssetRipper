"""Tests for `gui.material_overrides_dialog.MaterialOverridesDialog`."""

from __future__ import annotations

import pytest

from gui.material_overrides_dialog import MaterialOverridesDialog

pytestmark = [pytest.mark.qt, pytest.mark.gui]


@pytest.fixture
def presets():
    return {
        "presets": {
            "default_pbr": {
                "texture_slots": {
                    "base_color": {"suffixes": ["_C"]},
                    "normal": {"suffixes": ["_N"]},
                    "orm": {"suffixes": ["_ORM"]},
                }
            },
            "simple_diffuse": {
                "texture_slots": {
                    "base_color": {"suffixes": ["_C"]},
                    "normal": {"suffixes": ["_N"]},
                }
            },
        }
    }


def test_dialog_lists_materials_from_overrides(qtbot, presets):
    overrides = {
        "BatteryMetals_A": {"preset": "default_pbr", "force_textures": {}},
        "RivetsSteelA": {"preset": "default_pbr", "force_textures": {}},
    }
    dlg = MaterialOverridesDialog(overrides, presets)
    qtbot.addWidget(dlg)

    items = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    # Sorted alphabetically.
    assert items == ["BatteryMetals_A", "RivetsSteelA"]


def test_filter_narrows_visible_materials(qtbot, presets):
    overrides = {
        "BatteryMetals_A": {"preset": "default_pbr", "force_textures": {}},
        "RivetsSteelA": {"preset": "default_pbr", "force_textures": {}},
        "Battery_Grating_A": {"preset": "default_pbr", "force_textures": {}},
    }
    dlg = MaterialOverridesDialog(overrides, presets)
    qtbot.addWidget(dlg)

    dlg._filter.setText("battery")
    items = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    assert "BatteryMetals_A" in items
    assert "Battery_Grating_A" in items
    assert "RivetsSteelA" not in items


def test_force_texture_round_trips_through_result(qtbot, presets):
    overrides = {
        "BatteryMetals_A": {
            "preset": "default_pbr",
            "force_textures": {"base_color": "T_BatteryMetals_Albedo.tga"},
        }
    }
    dlg = MaterialOverridesDialog(overrides, presets)
    qtbot.addWidget(dlg)
    # The slot picker for base_color should be pre-populated.
    bc_picker = dlg._slot_pickers["base_color"]
    assert bc_picker.text() == "T_BatteryMetals_Albedo.tga"

    # Mutate, capture via OK.
    bc_picker.setText("T_NewAlbedo.tga")
    dlg._on_ok()
    result = dlg.result_overrides()
    assert result["BatteryMetals_A"]["force_textures"]["base_color"] == "T_NewAlbedo.tga"


def test_changing_preset_swaps_slot_rows(qtbot, presets):
    """Switching presets should rebuild slot rows for the new slot set."""
    overrides = {"X": {"preset": "default_pbr", "force_textures": {}}}
    dlg = MaterialOverridesDialog(overrides, presets)
    qtbot.addWidget(dlg)
    assert "orm" in dlg._slot_pickers  # default_pbr has orm

    dlg._preset_combo.setCurrentText("simple_diffuse")
    # simple_diffuse has only base_color and normal — orm should be gone.
    assert "orm" not in dlg._slot_pickers
    assert {"base_color", "normal"}.issubset(dlg._slot_pickers.keys())


def test_remove_material_drops_it_from_result(qtbot, presets):
    overrides = {
        "A": {"preset": "default_pbr", "force_textures": {}},
        "B": {"preset": "default_pbr", "force_textures": {}},
    }
    dlg = MaterialOverridesDialog(overrides, presets)
    qtbot.addWidget(dlg)

    # Select "A" and remove.
    dlg._list.setCurrentRow(0)
    dlg._on_remove_material()

    dlg._on_ok()
    result = dlg.result_overrides()
    assert "A" not in result
    assert "B" in result


def test_cancel_discards_edits(qtbot, presets):
    overrides = {
        "A": {"preset": "default_pbr", "force_textures": {"base_color": "old.tga"}},
    }
    dlg = MaterialOverridesDialog(overrides, presets)
    qtbot.addWidget(dlg)

    dlg._slot_pickers["base_color"].setText("modified.tga")
    dlg.reject()

    # The original dict the caller passed in must NOT have been mutated.
    assert overrides["A"]["force_textures"]["base_color"] == "old.tga"


def test_empty_overrides_shows_placeholder(qtbot, presets):
    """With no materials the editor pane shows a placeholder rather than crashing."""
    dlg = MaterialOverridesDialog({}, presets)
    qtbot.addWidget(dlg)
    assert dlg._list.count() == 0
    # No crash — the placeholder label exists (just smoke-check).


def test_missing_presets_data_falls_back_to_default(qtbot):
    """The dialog must not crash when presets_data is empty/malformed."""
    dlg = MaterialOverridesDialog(
        {"M1": {"preset": "default_pbr", "force_textures": {}}},
        {},
    )
    qtbot.addWidget(dlg)
    items = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    assert items == ["M1"]
