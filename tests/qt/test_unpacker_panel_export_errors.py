"""Tests for UnpackerPanel export-failure popup + suppression flag."""

from __future__ import annotations

import pytest

from PySide6.QtWidgets import QCheckBox, QMessageBox

from gui.unpacker_panel import UnpackerPanel


class TestExportFailurePopup:
    """Verify that export errors raise a modal and respect the suppress flag."""

    @pytest.fixture
    def panel(self, qtbot):
        p = UnpackerPanel()
        qtbot.addWidget(p)
        return p

    def test_suppress_flag_initialized_false(self, panel):
        assert panel._suppress_export_error_popup is False

    def test_export_done_with_failures_shows_popup(self, panel, mocker):
        spy = mocker.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok)
        failed = [{"path": "Game/Foo.uasset", "error": "Invalid FString length"}]
        panel._on_export_done(["Game/Bar.uasset"], failed)
        spy.assert_called_once()

    def test_export_done_no_failures_no_popup(self, panel, mocker):
        spy = mocker.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok)
        panel._on_export_done(["Game/Bar.uasset"], [])
        spy.assert_not_called()

    def test_popup_suppressed_when_flag_set(self, panel, mocker):
        panel._suppress_export_error_popup = True
        spy = mocker.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok)
        panel._on_export_done([], [{"path": "x", "error": "boom"}])
        spy.assert_not_called()

    def test_user_checking_box_sets_suppress_flag(self, panel, mocker):
        # The helper inspects checkBox().isChecked() after exec() returns,
        # so simulating "user ticked the box" = stub isChecked to True.
        mocker.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok)
        mocker.patch.object(QCheckBox, "isChecked", return_value=True)
        panel._on_export_done([], [{"path": "x", "error": "boom"}])
        assert panel._suppress_export_error_popup is True

    def test_unchecked_box_leaves_suppress_flag_false(self, panel, mocker):
        mocker.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok)
        mocker.patch.object(QCheckBox, "isChecked", return_value=False)
        panel._on_export_done([], [{"path": "x", "error": "boom"}])
        assert panel._suppress_export_error_popup is False

    def test_fatal_error_during_export_shows_popup(self, panel, mocker):
        panel._exporting = True
        spy = mocker.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok)
        panel._on_error("CLI crashed: version mismatch")
        spy.assert_called_once()
        assert panel._exporting is False

    def test_fatal_error_outside_export_no_popup(self, panel, mocker):
        panel._exporting = False
        spy = mocker.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok)
        panel._on_error("Mount failure")
        spy.assert_not_called()

    def test_fatal_error_respects_suppress_flag(self, panel, mocker):
        panel._exporting = True
        panel._suppress_export_error_popup = True
        spy = mocker.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok)
        panel._on_error("CLI crashed")
        spy.assert_not_called()
        # _exporting is still cleared so the UI re-enables export buttons
        assert panel._exporting is False

    def test_process_ended_resets_suppress_flag(self, panel):
        panel._suppress_export_error_popup = True
        panel._on_process_ended()
        assert panel._suppress_export_error_popup is False

    def test_load_from_profile_resets_suppress_flag(self, panel):
        panel._suppress_export_error_popup = True
        panel.load_from_profile({
            "game_dir": "",
            "ue_version": "GAME_UE5_4",
            "mappings_path": "",
            "unpack_output_dir": "",
            "aes_keys": [],
        })
        assert panel._suppress_export_error_popup is False
