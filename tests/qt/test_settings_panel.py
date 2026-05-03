"""Tests for `gui.settings_panel.SettingsDialog` (mocks QFileDialog).

After the per-profile path refactor, SettingsDialog only carries global
tooling paths: Blender Executable, Everything SDK DLL, Texture Presets JSON,
CUE4Parse CLI. Per-profile paths (game folder / mounted folder / output
folder) live in the Manage Profiles dialog and are exercised in
``test_profile_dialog.py``.
"""

from __future__ import annotations

import pytest

import config
from gui.widgets import PathPicker
from gui.settings_panel import SettingsDialog

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

    def _fake(parent, title, start=""):
        captured["called"] = (parent, title, start)
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
    config.set("blender_exe", r"C:\Blender\blender.exe")
    config.set("psk_addon_name", "addon.from.config")
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    assert dlg.blender_exe.text() == r"C:\Blender\blender.exe"
    assert dlg.addon_name.text() == "addon.from.config"


def test_settings_dialog_save_writes_to_config(qtbot, mock_qsettings, monkeypatch):
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    dlg.blender_exe.setText(r"C:\Blender\NEW\blender.exe")
    dlg.addon_name.setText("custom.addon.name")
    dlg.timeout.setValue(300)

    # Validation prompts on missing paths — auto-confirm "Save anyway".
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Yes),
    )

    with qtbot.waitSignal(dlg.settings_changed, timeout=1000):
        dlg._save()

    assert config.get("blender_exe") == r"C:\Blender\NEW\blender.exe"
    assert config.get("psk_addon_name") == "custom.addon.name"
    assert config.get_int("timeout_seconds") == 300


def test_settings_dialog_save_aborts_on_missing_path_confirm_no(qtbot, mock_qsettings, monkeypatch):
    """Saying No to the missing-path prompt should abort the save."""
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    dlg.blender_exe.setText(r"C:\Nope\blender.exe")
    config.set("blender_exe", r"C:\Old\blender.exe")
    config.set("psk_addon_name", "old.addon")

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.No),
    )

    dlg._save()

    # Old value should remain untouched because save was aborted.
    assert config.get("blender_exe") == r"C:\Old\blender.exe"
    assert config.get("psk_addon_name") == "old.addon"


def test_settings_dialog_does_not_expose_per_profile_path_fields(qtbot, mock_qsettings):
    """Per-profile path fields must not appear on the global Settings dialog."""
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    for removed in ("game_folder", "output_dir", "unpack_output_dir"):
        assert not hasattr(dlg, removed), (
            f"SettingsDialog still exposes {removed!r}; should be in Manage Profiles"
        )


def test_open_presets_warns_on_missing_path(qtbot, mock_qsettings, monkeypatch):
    """Opening a non-existent presets file should warn instead of crashing."""
    from PySide6.QtWidgets import QMessageBox

    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    dlg.presets_path.setText(r"C:\Nope\does_not_exist.json")

    captured: dict = {}
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **kw: captured.setdefault("called", True)),
    )

    dlg._open_presets()
    assert captured.get("called") is True


def test_settings_dialog_timeout_clamped(qtbot, mock_qsettings):
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    # The QSpinBox range is 10..3600
    dlg.timeout.setValue(99999)
    assert dlg.timeout.value() <= 3600
    dlg.timeout.setValue(-50)
    assert dlg.timeout.value() >= 10
