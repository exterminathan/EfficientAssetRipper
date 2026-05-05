"""Tests for UnpackerPanel auto-detect UE version behavior."""

from __future__ import annotations

import pytest
from unittest.mock import PropertyMock, patch

from gui.unpacker_panel import UnpackerPanel


class TestUnpackerPanelVersionDetection:
    """Test the UE version auto-detection flag and slot behavior."""

    @pytest.fixture
    def panel(self, qtbot):
        p = UnpackerPanel()
        qtbot.addWidget(p)
        return p

    def test_ue_version_user_set_flag_initialized_false(self, panel):
        """Test that _ue_version_user_set starts as False."""
        assert panel._ue_version_user_set is False

    def test_ue_version_user_set_flag_on_combo_change(self, panel):
        """Test that combo box changes set the user_set flag."""
        assert panel._ue_version_user_set is False
        panel._ue_version_combo.setCurrentText("GAME_UE4_27")
        assert panel._ue_version_user_set is True

    def test_ue_version_user_set_flag_on_edit(self, panel):
        """Test that manual edit of combo also sets the user_set flag."""
        assert panel._ue_version_user_set is False
        panel._ue_version_combo.lineEdit().setText("GAME_UE4_24")
        assert panel._ue_version_user_set is True

    def test_load_from_profile_resets_user_set_flag(self, panel):
        """Test that loading a profile resets the user_set flag."""
        panel._ue_version_user_set = True
        profile = {
            "game_dir": "/path/to/game",
            "ue_version": "GAME_UE5_4",
            "mappings_path": "",
            "unpack_output_dir": "",
            "aes_keys": [],
        }
        panel.load_from_profile(profile)
        assert panel._ue_version_user_set is False

    def test_on_version_detected_auto_fill_when_not_user_set(self, panel, qtbot):
        """Test that auto-detection fills combo when user hasn't manually changed it."""
        assert panel._ue_version_user_set is False
        panel._on_version_detected("GAME_UE4_12", "Game-Win64-Shipping.exe", "4.12.5.0")
        assert panel._ue_version_combo.currentText() == "GAME_UE4_12"

    def test_on_version_detected_does_not_overwrite_user_choice(self, panel):
        """Test that auto-detection does NOT overwrite user's manual selection."""
        panel._ue_version_combo.setCurrentText("GAME_UE4_27")
        panel._ue_version_user_set = True
        original_text = panel._ue_version_combo.currentText()

        panel._on_version_detected("GAME_UE4_12", "Game-Win64-Shipping.exe", "4.12.5.0")
        assert panel._ue_version_combo.currentText() == original_text

    def test_on_version_detected_logs_success(self, panel, qtbot):
        """Test that successful detection emits a log message."""
        log_messages = []
        panel.log_message.connect(lambda m, l: log_messages.append((m, l)))

        panel._on_version_detected("GAME_UE5_3", "Game-Win64-Shipping.exe", "5.3.2.0")

        assert len(log_messages) == 1
        msg, level = log_messages[0]
        assert "Auto-detected UE version: GAME_UE5_3" in msg
        assert "Game-Win64-Shipping.exe" in msg
        assert level == "info"

    def test_on_version_detected_logs_failure(self, panel, qtbot):
        """Test that detection failure emits a log message."""
        log_messages = []
        panel.log_message.connect(lambda m, l: log_messages.append((m, l)))

        panel._on_version_detected("", "", "No executable found")

        assert len(log_messages) == 1
        msg, level = log_messages[0]
        assert "UE version detection failed" in msg
        assert "No executable found" in msg
        assert level == "info"

    def test_browse_game_dir_resets_user_set_flag(self, panel, qtbot, mocker):
        """Test that browsing a new game dir resets the user_set flag."""
        panel._ue_version_user_set = True
        panel._unpacker._proc = None  # mock not running

        # Mock QFileDialog.getExistingDirectory to return a path
        mocker.patch(
            "gui.unpacker_panel.QFileDialog.getExistingDirectory",
            return_value="/some/game/path"
        )

        panel._browse_game_dir()
        assert panel._ue_version_user_set is False
        assert panel._game_dir_edit.text() == "/some/game/path"

    def test_browse_game_dir_triggers_detection_if_running(self, panel, mocker):
        """Test that browsing triggers detect_ue_version if CLI is running."""
        mock_detect = mocker.patch.object(panel._unpacker, "detect_ue_version")

        with patch.object(
            type(panel._unpacker), "is_running", new_callable=PropertyMock, return_value=True
        ):
            mocker.patch(
                "gui.unpacker_panel.QFileDialog.getExistingDirectory",
                return_value="/some/game/path"
            )

            panel._browse_game_dir()
            mock_detect.assert_called_once_with("/some/game/path")

    def test_browse_game_dir_does_not_trigger_detection_if_not_running(self, panel, mocker):
        """Test that browsing doesn't trigger detection if CLI is not running."""
        mock_detect = mocker.patch.object(panel._unpacker, "detect_ue_version")

        with patch.object(
            type(panel._unpacker), "is_running", new_callable=PropertyMock, return_value=False
        ):
            mocker.patch(
                "gui.unpacker_panel.QFileDialog.getExistingDirectory",
                return_value="/some/game/path"
            )

            panel._browse_game_dir()
            mock_detect.assert_not_called()
