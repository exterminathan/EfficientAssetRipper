"""Tests for UnpackerPanel auto-detect UE version behavior.

The Unpacker no longer hosts its own editor for the UE version — the value
lives in the active profile and is shown read-only on the Mount toolbar.
The CLI's version-detection result still lands here so the displayed value
matches what's actually being used.
"""

from __future__ import annotations

import pytest

from gui.unpacker_panel import UnpackerPanel


class TestUnpackerPanelVersionDetection:
    """Test the read-only UE version display + auto-detection slot."""

    @pytest.fixture
    def panel(self, qtbot):
        p = UnpackerPanel()
        qtbot.addWidget(p)
        return p

    def test_ue_version_combo_is_disabled(self, panel):
        """Combo is read-only/disabled — editing happens in Manage Profiles."""
        assert panel._ue_version_combo.isEnabled() is False

    def test_game_dir_edit_is_readonly(self, panel):
        """Game folder is a read-only display in the Mount toolbar."""
        assert panel._game_dir_edit.isReadOnly() is True

    def test_mappings_edit_is_hidden(self, panel):
        """Mappings is sourced from the profile, not shown in the toolbar."""
        assert panel._mappings_edit.isVisible() is False

    def test_on_version_detected_updates_combo(self, panel, qtbot):
        """A successful CLI auto-detection updates the displayed value."""
        panel._on_version_detected("GAME_UE4_12", "Game-Win64-Shipping.exe", "4.12.5.0")
        assert panel._ue_version_combo.currentText() == "GAME_UE4_12"

    def test_on_version_detected_with_unknown_version_still_displays(self, panel):
        """A version not in the static list is still shown so the user knows what the CLI mounted."""
        panel._on_version_detected("GAME_FuturisticBuild", "Game.exe", "9.9.9.9")
        assert panel._ue_version_combo.currentText() == "GAME_FuturisticBuild"

    def test_on_version_detected_logs_success(self, panel, qtbot):
        """Successful detection emits a log message."""
        log_messages = []
        panel.log_message.connect(lambda m, l: log_messages.append((m, l)))

        panel._on_version_detected("GAME_UE5_3", "Game-Win64-Shipping.exe", "5.3.2.0")

        assert len(log_messages) == 1
        msg, level = log_messages[0]
        assert "Auto-detected UE version: GAME_UE5_3" in msg
        assert "Game-Win64-Shipping.exe" in msg
        assert level == "info"

    def test_on_version_detected_logs_failure(self, panel, qtbot):
        """Detection failure emits a log message and leaves the combo alone."""
        log_messages = []
        panel.log_message.connect(lambda m, l: log_messages.append((m, l)))

        before = panel._ue_version_combo.currentText()
        panel._on_version_detected("", "", "No executable found")

        assert len(log_messages) == 1
        msg, level = log_messages[0]
        assert "UE version detection failed" in msg
        assert "No executable found" in msg
        assert level == "info"
        # Combo unchanged on failed detection.
        assert panel._ue_version_combo.currentText() == before
