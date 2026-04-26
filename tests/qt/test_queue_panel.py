"""Tests for `gui.queue_panel.QueuePanel` (signals + table state)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.asset_scanner import AssetEntry
from gui.queue_panel import QueuePanel

pytestmark = pytest.mark.qt


def _entry(name: str, sub: str = "Sub") -> AssetEntry:
    return AssetEntry(
        psk_path=Path(rf"C:\Game\{sub}\{name}.psk"),
        name=name,
        mesh_props_found=True,
    )


def test_queue_panel_constructs(qtbot):
    q = QueuePanel()
    qtbot.addWidget(q)
    assert q._table.rowCount() == 0


def test_add_assets_increases_row_count(qtbot):
    q = QueuePanel()
    qtbot.addWidget(q)
    q.add_to_queue([_entry("A"), _entry("B")])
    assert q._table.rowCount() == 2


def test_dedup_by_psk_path(qtbot):
    """Adding the same psk_path twice should yield only one row."""
    q = QueuePanel()
    qtbot.addWidget(q)
    a = _entry("Dup")
    q.add_to_queue([a])
    q.add_to_queue([a])
    assert q._table.rowCount() == 1


def test_clear_queue_resets_state(qtbot):
    q = QueuePanel()
    qtbot.addWidget(q)
    q.add_to_queue([_entry("A"), _entry("B")])
    q.clear_queue()
    assert q._table.rowCount() == 0
    assert q._items == []


def test_process_requested_signal_fires(qtbot):
    q = QueuePanel()
    qtbot.addWidget(q)
    with qtbot.waitSignal(q.process_requested, timeout=1000):
        q._process_btn.click()


def test_cancel_requested_signal_fires(qtbot):
    q = QueuePanel()
    qtbot.addWidget(q)
    q.set_processing(True)
    with qtbot.waitSignal(q.cancel_requested, timeout=1000):
        q._cancel_btn.click()


def test_on_job_completed_marks_done(qtbot):
    q = QueuePanel()
    qtbot.addWidget(q)
    q.add_to_queue([_entry("A"), _entry("B")])
    q.begin_processing(0, 2)
    q.on_job_started(0, "A")
    q.on_job_completed(0, "A", success=True)
    assert q._table.item(0, 1).text() == "Done"


def test_on_job_completed_marks_failed(qtbot):
    q = QueuePanel()
    qtbot.addWidget(q)
    q.add_to_queue([_entry("A")])
    q.begin_processing(0, 1)
    q.on_job_completed(0, "A", success=False)
    assert q._table.item(0, 1).text() == "Failed"


def test_get_pending_assets_returns_only_pending(qtbot):
    q = QueuePanel()
    qtbot.addWidget(q)
    q.add_to_queue([_entry("X"), _entry("Y")])
    q.begin_processing(0, 1)
    q.on_job_completed(0, "X", success=True)
    pending = q.get_pending_assets()
    assert [p.name for p in pending] == ["Y"]
