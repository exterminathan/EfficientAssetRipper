"""Tests for `gui.settings_panel.SettingsDialog` (mocks QFileDialog)."""

from __future__ import annotations

import pytest

import config
from gui.settings_panel import PathPicker, SettingsDialog

pytestmark = [pytest.mark.qt, pytest.mark.gui]


def test_path_picker_text_round_trip(qtbot):
    p = PathPicker(mode="folder")
    qtbot.addWidget(p)
    p.setText(r"C:\Picked")
    assert p.text() == r"C:\Picked"


def test_path_picker_browse_uses_qfiledialog_static_mock(qtbot, monkeypatch):
    """Browse should call QFileDialog.getExistingDirectory in folder mode."""
    from PySide6.QtWidgets import QFileDialog

    captured: dict = {}

    def _fake(parent, title):
        captured["called"] = (parent, title)
        return r"C:\From\Mock"

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(_fake))
    p = PathPicker(mode="folder")
    qtbot.addWidget(p)
    p._browse()
    assert captured.get("called") is not None
    assert p.text() == r"C:\From\Mock"


def test_settings_dialog_constructs(qtbot, mock_qsettings):
    """Building the SettingsDialog with stubbed config should not raise."""
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == "Settings"


def test_settings_dialog_load_populates_fields_from_config(qtbot, mock_qsettings):
    config.set("game_folder", r"C:\Games\X")
    config.set("psk_addon_name", "addon.from.config")
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    assert dlg.game_folder.text() == r"C:\Games\X"
    assert dlg.addon_name.text() == "addon.from.config"


def test_settings_dialog_save_writes_to_config(qtbot, mock_qsettings):
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    dlg.game_folder.setText(r"C:\Games\NEW")
    dlg.addon_name.setText("custom.addon.name")
    dlg.timeout.setValue(300)

    with qtbot.waitSignal(dlg.settings_changed, timeout=1000):
        dlg._save()

    assert config.get("game_folder") == r"C:\Games\NEW"
    assert config.get("psk_addon_name") == "custom.addon.name"
    assert config.get_int("timeout_seconds") == 300


def test_settings_dialog_timeout_clamped(qtbot, mock_qsettings):
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    # The QSpinBox range is 10..3600
    dlg.timeout.setValue(99999)
    assert dlg.timeout.value() <= 3600
    dlg.timeout.setValue(-50)
    assert dlg.timeout.value() >= 10
