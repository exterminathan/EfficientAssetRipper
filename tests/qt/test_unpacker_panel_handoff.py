"""Tests for UnpackerPanel session-scoped Send-to-Queue hand-off.

Regression: the hand-off button used to rglob the entire output directory and
emit *every* PSK in it — so a single new export would silently scoop up
thousands of files from prior sessions. The button now tracks only what THIS
session's exports actually wrote, computed as the diff against a snapshot
taken at export-start.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from PySide6.QtWidgets import QMessageBox

from gui.unpacker_panel import UnpackerPanel


@pytest.fixture
def panel(qtbot):
    p = UnpackerPanel()
    qtbot.addWidget(p)
    return p


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_handoff_only_emits_session_extracted_psks(panel, tmp_path, qtbot):
    """Pre-existing PSKs in output dir must NOT be in the hand-off."""
    out = tmp_path / "out"
    out.mkdir()
    pre = _touch(out / "old" / "OldAsset.pskx")  # left over from prior session

    panel._output_dir_edit.setText(str(out))
    panel._export_output_dir = str(out)

    # Simulate "begin export" — snapshot captures the pre-existing file.
    panel._begin_export()
    assert pre.resolve() in panel._pre_export_psks

    # Simulate the CLI writing a NEW psk during this export.
    new = _touch(out / "fresh" / "NewAsset.pskx")

    emitted: list[list[Path]] = []
    panel.psk_extracted.connect(lambda paths: emitted.append(list(paths)))

    panel._on_export_done(["Game/Foo.uasset"], [])

    # Button should reflect the diff (1 new file), not 2.
    assert panel._handoff_btn.isEnabled()
    assert "Send 1 PSKs" in panel._handoff_btn.text()
    assert panel._session_handoff_psks == [new.resolve()]

    panel._handoff_psks()
    assert emitted == [[new.resolve()]]
    # After hand-off the session list is cleared and button reset.
    assert panel._session_handoff_psks == []
    assert not panel._handoff_btn.isEnabled()


def test_handoff_accumulates_across_multiple_exports(panel, tmp_path):
    """Two back-to-back exports should accumulate; one click queues both."""
    out = tmp_path / "out"
    out.mkdir()
    panel._output_dir_edit.setText(str(out))
    panel._export_output_dir = str(out)

    # First export run — writes A.
    panel._begin_export()
    a = _touch(out / "A.pskx")
    panel._on_export_done(["Game/A"], [])
    assert panel._session_handoff_psks == [a.resolve()]

    # Second export run — writes B. A is already on disk; the snapshot at
    # the start of run 2 includes A, so the diff for run 2 is just {B}.
    panel._begin_export()
    assert a.resolve() in panel._pre_export_psks  # snapshot picked up A
    b = _touch(out / "B.pskx")
    panel._on_export_done(["Game/B"], [])

    # Both A (from run 1) and B (from run 2) should be queued together.
    assert panel._session_handoff_psks == [a.resolve(), b.resolve()]
    assert "Send 2 PSKs" in panel._handoff_btn.text()


def test_handoff_dedupes_re_export_of_same_file(panel, tmp_path):
    """Re-exporting the same file shouldn't double-count it."""
    out = tmp_path / "out"
    out.mkdir()
    panel._output_dir_edit.setText(str(out))
    panel._export_output_dir = str(out)

    panel._begin_export()
    a = _touch(out / "A.pskx")
    panel._on_export_done(["Game/A"], [])
    assert panel._session_handoff_psks == [a.resolve()]

    # User exports A again (overwrite). Diff against the new snapshot would
    # be empty — A was already there. The session list shouldn't grow.
    panel._begin_export()
    a.write_bytes(b"v2")
    panel._on_export_done(["Game/A"], [])
    assert panel._session_handoff_psks == [a.resolve()]


def test_handoff_with_no_new_psks_shows_info_dialog(panel, tmp_path, mocker):
    """Clicking the button with an empty session list nags the user."""
    out = tmp_path / "out"
    out.mkdir()
    _touch(out / "Stale.pskx")  # exists on disk but never emitted by us
    panel._output_dir_edit.setText(str(out))
    panel._export_output_dir = str(out)

    spy = mocker.patch.object(
        QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok
    )
    emitted: list[list[Path]] = []
    panel.psk_extracted.connect(lambda paths: emitted.append(list(paths)))

    panel._handoff_psks()
    spy.assert_called_once()
    assert emitted == []  # nothing emitted when session list is empty


def test_pre_existing_psks_never_leak_into_handoff(panel, tmp_path):
    """The original bug: 4000 stale PSKs in output dir, user exports 1.

    Old behavior would queue all 4001. New behavior queues exactly 1.
    """
    out = tmp_path / "out"
    out.mkdir()
    # Simulate 4000 leftover PSKs from prior sessions.
    for i in range(20):  # 20 is enough to demonstrate; full 4000 is wasteful
        _touch(out / f"sub{i}" / f"Old_{i}.pskx")

    panel._output_dir_edit.setText(str(out))
    panel._export_output_dir = str(out)

    panel._begin_export()
    # User exports exactly one new asset.
    new = _touch(out / "fresh" / "JustOne.pskx")
    panel._on_export_done(["Game/JustOne"], [])

    assert len(panel._session_handoff_psks) == 1
    assert panel._session_handoff_psks[0] == new.resolve()


def test_re_export_of_pre_existing_file_updates_button(panel, tmp_path):
    """Path-only diffing missed re-exports — overwriting an existing file
    on disk leaves the path unchanged, so the diff was empty and the button
    never updated. Tracking mtime alongside path catches this.
    """
    import os
    import time

    out = tmp_path / "out"
    out.mkdir()
    # User had this asset on disk from a prior app session.
    a = _touch(out / "Asset.pskx")
    old_mtime = a.stat().st_mtime
    # Backdate it so the eventual rewrite gives a clearly-newer mtime even
    # on filesystems with coarse (1s / 2s) timestamp granularity.
    os.utime(a, (old_mtime - 5, old_mtime - 5))

    panel._output_dir_edit.setText(str(out))
    panel._export_output_dir = str(out)

    # Snapshot picks up Asset.pskx with its (backdated) mtime.
    panel._begin_export()
    snapshot_mtime = panel._pre_export_psks[a.resolve()]

    # Simulate the CLI overwriting it during this export — same path, fresh mtime.
    a.write_bytes(b"v2")
    new_mtime = a.stat().st_mtime
    assert new_mtime > snapshot_mtime, (
        "test setup is broken — overwrite didn't advance mtime"
    )

    panel._on_export_done(["Game/Asset"], [])

    # Button must reflect the re-export — old behavior left it disabled.
    assert panel._session_handoff_psks == [a.resolve()]
    assert panel._handoff_btn.isEnabled()
    assert "Send 1 PSKs" in panel._handoff_btn.text()


def test_untouched_pre_existing_files_stay_invisible(panel, tmp_path):
    """If an export run touches NOTHING in the output dir, the button stays
    disabled — no stale files should sneak in just because they're there.
    """
    out = tmp_path / "out"
    out.mkdir()
    _touch(out / "Untouched.pskx")

    panel._output_dir_edit.setText(str(out))
    panel._export_output_dir = str(out)

    panel._begin_export()
    # Export "completes" without writing anything new and without touching
    # the existing file.
    panel._on_export_done([], [])

    assert panel._session_handoff_psks == []
    assert not panel._handoff_btn.isEnabled()
