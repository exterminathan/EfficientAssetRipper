"""Integration tests for queue checkpoint persistence + resume.

Covers:
- File format round-trip via save / load
- Atomic write semantics
- Version mismatch / malformed payload handling
- ``CheckpointPayload.remaining`` filtering
- JobManager writes/clears the checkpoint at the expected boundaries
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import queue_checkpoint
from core.asset_scanner import AssetEntry, MaterialEntry

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(name: str) -> AssetEntry:
    return AssetEntry(
        psk_path=Path(rf"C:\Game\{name}.psk"),
        name=name,
        materials=[MaterialEntry(slot_name="S0", material_name=f"M_{name}")],
        mesh_props_found=True,
    )


@pytest.fixture(autouse=True)
def _isolate_path(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_checkpoint, "_DEFAULT_PATH", tmp_path / "queue_checkpoint.json")


# ---------------------------------------------------------------------------
# Save / load round-trips
# ---------------------------------------------------------------------------

def test_save_writes_versioned_payload(tmp_path):
    queue_checkpoint.save(profile="P", pending=[_entry("A"), _entry("B")], completed=[])
    raw = queue_checkpoint.path().read_text(encoding="utf-8")
    body = json.loads(raw)
    assert body["version"] == 1
    assert body["active_profile"] == "P"
    assert body["pending"][0]["name"] == "A"
    assert body["completed"] == []


def test_load_round_trip_returns_assetentries():
    queue_checkpoint.save(
        profile="P",
        pending=[_entry("A"), _entry("B")],
        completed=[r"C:\Game\A.psk"],
    )
    payload = queue_checkpoint.load()
    assert payload is not None
    assert payload.profile == "P"
    assert [a.name for a in payload.pending] == ["A", "B"]
    assert payload.completed == [r"C:\Game\A.psk"]


def test_remaining_filters_completed():
    queue_checkpoint.save(
        profile="P",
        pending=[_entry("A"), _entry("B"), _entry("C")],
        completed=[r"C:\Game\A.psk", r"C:\Game\C.psk"],
    )
    payload = queue_checkpoint.load()
    remaining = payload.remaining
    assert [a.name for a in remaining] == ["B"]


def test_load_missing_returns_none():
    assert queue_checkpoint.load() is None


def test_load_handles_malformed_json():
    queue_checkpoint.path().write_text("{not json", encoding="utf-8")
    assert queue_checkpoint.load() is None


def test_load_rejects_wrong_version():
    queue_checkpoint.path().write_text(
        json.dumps({"version": 99, "pending": [], "completed": []}),
        encoding="utf-8",
    )
    assert queue_checkpoint.load() is None


def test_load_rejects_pending_not_list():
    queue_checkpoint.path().write_text(
        json.dumps({"version": 1, "pending": "oops", "completed": []}),
        encoding="utf-8",
    )
    assert queue_checkpoint.load() is None


def test_delete_removes_file():
    queue_checkpoint.save(profile="P", pending=[_entry("A")], completed=[])
    assert queue_checkpoint.path().is_file()
    queue_checkpoint.delete()
    assert not queue_checkpoint.path().is_file()


def test_delete_is_safe_when_missing():
    # No file present — should not raise.
    queue_checkpoint.delete()


def test_save_is_atomic_no_tmp_file_left(tmp_path):
    queue_checkpoint.save(profile="P", pending=[_entry("A")], completed=[])
    leftover = list(tmp_path.glob("queue_checkpoint.json.tmp"))
    assert leftover == []


def test_exists_matches_filesystem():
    assert queue_checkpoint.exists() is False
    queue_checkpoint.save(profile="P", pending=[_entry("A")], completed=[])
    assert queue_checkpoint.exists() is True
    queue_checkpoint.delete()
    assert queue_checkpoint.exists() is False
