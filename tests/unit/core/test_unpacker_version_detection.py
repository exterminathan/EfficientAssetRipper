"""Tests for UE version auto-detection signal handling."""

from __future__ import annotations

import pytest

from core.unpacker import UnpackerProcess


class TestVersionDetectedSignal:
    """Test the version_detected signal emission and parameter handling."""

    def test_version_detected_signal_success(self, qtbot):
        """Test that version_detected signal emits with detected version."""
        unpacker = UnpackerProcess()
        with qtbot.waitSignal(unpacker.version_detected, timeout=500) as blocker:
            unpacker.version_detected.emit("GAME_UE4_12", "GameName-Win64-Shipping.exe", "4.12.5.0")

        assert blocker.signal_triggered
        args = blocker.args
        assert args[0] == "GAME_UE4_12"
        assert args[1] == "GameName-Win64-Shipping.exe"
        assert args[2] == "4.12.5.0"

    def test_version_detected_signal_failure(self, qtbot):
        """Test that version_detected signal emits with None on detection failure."""
        unpacker = UnpackerProcess()
        with qtbot.waitSignal(unpacker.version_detected, timeout=500) as blocker:
            unpacker.version_detected.emit("", "", "No executable found")

        assert blocker.signal_triggered
        args = blocker.args
        assert args[0] == ""
        assert args[1] == ""
        assert args[2] == "No executable found"

    def test_version_detected_dispatch_success(self, qtbot):
        """Test that _dispatch routes version_detected messages correctly."""
        unpacker = UnpackerProcess()
        with qtbot.waitSignal(unpacker.version_detected, timeout=500) as blocker:
            msg = {
                "type": "version_detected",
                "suggested": "GAME_UE5_3",
                "source_exe": "Satisfactory-Win64-Shipping.exe",
                "file_version": "5.3.2.0"
            }
            unpacker._dispatch(msg)

        assert blocker.signal_triggered
        args = blocker.args
        assert args[0] == "GAME_UE5_3"
        assert args[1] == "Satisfactory-Win64-Shipping.exe"
        assert args[2] == "5.3.2.0"

    def test_version_detected_dispatch_failure(self, qtbot):
        """Test that _dispatch handles detection failure correctly."""
        unpacker = UnpackerProcess()
        with qtbot.waitSignal(unpacker.version_detected, timeout=500) as blocker:
            msg = {
                "type": "version_detected",
                "suggested": None,
                "reason": "No matching exe found in game directory"
            }
            unpacker._dispatch(msg)

        assert blocker.signal_triggered
        args = blocker.args
        assert args[0] == ""
        assert args[1] == ""
        assert "No matching exe found" in args[2]

    def test_version_detected_with_missing_fields(self, qtbot):
        """Test that _dispatch handles missing fields gracefully."""
        unpacker = UnpackerProcess()
        with qtbot.waitSignal(unpacker.version_detected, timeout=500) as blocker:
            msg = {"type": "version_detected"}
            unpacker._dispatch(msg)

        assert blocker.signal_triggered
        args = blocker.args
        # All fields should be empty strings when not provided
        assert args[0] == ""
        assert args[1] == ""
        assert args[2] == ""
