"""Unit tests for `core.blender_runner._process_status` (pure status parser)."""

from __future__ import annotations

import pytest

from core.blender_runner import BlenderResult, _process_status

pytestmark = pytest.mark.unit


def test_process_status_completed_sets_counts():
    result = BlenderResult(success=False)
    _process_status(
        {"status": "completed", "materials_processed": 3, "materials_failed": 1},
        result,
    )
    assert result.materials_processed == 3
    assert result.materials_failed == 1


def test_process_status_error_sets_message():
    result = BlenderResult(success=False)
    _process_status({"status": "error", "message": "PSK load failed"}, result)
    assert result.error_message == "PSK load failed"


def test_process_status_warning_appended():
    result = BlenderResult(success=False)
    _process_status({"status": "warning", "message": "Slot has no manifest entry"}, result)
    _process_status({"status": "warning", "message": "Texture missing"}, result)
    assert result.warnings == ["Slot has no manifest entry", "Texture missing"]


def test_process_status_unknown_status_is_noop():
    result = BlenderResult(success=False, materials_processed=99)
    _process_status({"status": "unknown_thing", "stuff": 1}, result)
    assert result.materials_processed == 99
    assert result.error_message == ""
    assert result.warnings == []


def test_process_status_completed_default_zero_when_keys_missing():
    result = BlenderResult(success=False)
    _process_status({"status": "completed"}, result)
    assert result.materials_processed == 0
    assert result.materials_failed == 0
