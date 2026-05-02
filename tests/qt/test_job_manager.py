"""Tests for `core.job_manager.JobManager` (QThread, mocked Blender subprocess)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.asset_scanner import AssetEntry, MaterialEntry
from core.blender_runner import BlenderResult
from core.job_manager import JobManager

pytestmark = pytest.mark.qt


def _entry(name: str) -> AssetEntry:
    return AssetEntry(
        psk_path=Path(rf"C:\Game\{name}.psk"),
        name=name,
        materials=[MaterialEntry(slot_name="S0", material_name=f"M_{name}")],
        mesh_props_found=True,
    )


def _start_and_wait(qtbot, jm: JobManager, *, timeout_ms: int = 5000):
    """Run the JobManager thread synchronously and wait for queue_finished."""
    with qtbot.waitSignal(jm.queue_finished, timeout=timeout_ms):
        jm.start()
    jm.wait(timeout_ms)


def test_run_completes_all_assets_when_blender_succeeds(
    qtbot, mock_blender_run, tmp_path
):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    jm = JobManager(
        assets=[_entry("A"), _entry("B")],
        blender_exe="fake-blender",
        output_dir=str(output_dir),
        addon_name="addon.x",
        timeout=30,
    )
    _start_and_wait(qtbot, jm)
    assert len(jm.results) == 2
    assert all(r.success for r in jm.results)


def test_queue_finished_carries_correct_counts(qtbot, mock_blender_run, tmp_path):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    jm = JobManager(
        assets=[_entry("A"), _entry("B")],
        blender_exe="fake-blender",
        output_dir=str(output_dir),
        addon_name="addon.x",
    )
    received = {}

    def _on_finished(total, succeeded, failed):
        received["total"] = total
        received["succeeded"] = succeeded
        received["failed"] = failed

    jm.queue_finished.connect(_on_finished)
    _start_and_wait(qtbot, jm)
    assert received == {"total": 2, "succeeded": 2, "failed": 0}


def test_blender_failure_continues_to_next_asset(qtbot, mock_blender_run, tmp_path):
    """If one asset fails, the loop should still process the next."""
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    # First call returns failure, second returns success
    mock_blender_run["responses"].append(
        BlenderResult(success=False, error_message="boom", return_code=1)
    )
    mock_blender_run["responses"].append(
        BlenderResult(success=True, materials_processed=1, return_code=0)
    )

    jm = JobManager(
        assets=[_entry("Bad"), _entry("Good")],
        blender_exe="fake-blender",
        output_dir=str(output_dir),
        addon_name="addon.x",
    )
    _start_and_wait(qtbot, jm)
    assert len(jm.results) == 2
    assert jm.results[0].success is False
    assert jm.results[1].success is True


def test_log_file_written_to_install_logs_dir(qtbot, mock_blender_run, tmp_path, monkeypatch):
    """Job logs land under base_dir()/logs, regardless of output_dir shape."""
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    # Redirect base_dir() so this test doesn't pollute the real install logs/.
    fake_base = tmp_path / "install"
    fake_base.mkdir()
    import core.job_manager as jm_mod
    monkeypatch.setattr(jm_mod, "base_dir", lambda: fake_base)

    jm = JobManager(
        assets=[_entry("LogMe")],
        blender_exe="fake-blender",
        output_dir=str(output_dir),
        addon_name="addon.x",
    )
    _start_and_wait(qtbot, jm)

    logs_dir = fake_base / "logs"
    assert logs_dir.is_dir()
    log_files = list(logs_dir.glob("batch_*.log"))
    assert len(log_files) >= 1
    # Each line is valid JSON
    for line in log_files[0].read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            assert "asset" in entry
            assert "success" in entry


@pytest.mark.parametrize(
    "weird_output_dir",
    [
        # Drive root — old code would write logs at C:\logs.
        "C:/",
        # UNC path — old code would write \\server\logs.
        "\\\\server\\share\\out",
        # Relative path — old code would write logs/.. relative to cwd.
        "outputs",
    ],
)
def test_log_file_path_independent_of_output_dir_shape(
    qtbot, mock_blender_run, tmp_path, monkeypatch, weird_output_dir
):
    fake_base = tmp_path / "install"
    fake_base.mkdir()
    import core.job_manager as jm_mod
    monkeypatch.setattr(jm_mod, "base_dir", lambda: fake_base)

    jm = JobManager(
        assets=[_entry("X")],
        blender_exe="fake-blender",
        output_dir=weird_output_dir,
        addon_name="addon.x",
    )
    _start_and_wait(qtbot, jm)
    # Logs went into the install-rooted dir, not under the weird output_dir.
    assert (fake_base / "logs").is_dir()
    assert any((fake_base / "logs").glob("batch_*.log"))


def test_log_entries_redact_aes_key_fields(qtbot, mock_blender_run, tmp_path, monkeypatch):
    """Even if a log entry happens to carry a key, the file must not echo it."""
    fake_base = tmp_path / "install"
    fake_base.mkdir()
    import core.job_manager as jm_mod
    monkeypatch.setattr(jm_mod, "base_dir", lambda: fake_base)

    secret = "a" * 64

    # Inject an AssetEntry whose name embeds a key-shaped blob; the redactor
    # uses field-name detection so we need a sensitive field. Patch the
    # log entry construction to add one.
    real_write = jm_mod.JobManager._write_log_entry

    def _wrapped(self, log_path, idx, total, asset, result):
        # Mimic a future change that tucks AES keys into the entry.
        from datetime import datetime
        from core.log_redaction import redact_sensitive
        entry = {
            "asset": asset.name,
            "success": result.success,
            "aes_key": secret,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            import json as _json
            f.write(_json.dumps(redact_sensitive(entry)) + "\n")

    monkeypatch.setattr(jm_mod.JobManager, "_write_log_entry", _wrapped)

    jm = JobManager(
        assets=[_entry("Sensitive")],
        blender_exe="fake-blender",
        output_dir=str(tmp_path / "out"),
        addon_name="addon.x",
    )
    _start_and_wait(qtbot, jm)

    log_files = list((fake_base / "logs").glob("batch_*.log"))
    assert log_files
    body = log_files[0].read_text(encoding="utf-8")
    assert secret not in body
    assert "REDACTED" in body


def test_run_emits_job_started_then_completed_per_asset(
    qtbot, mock_blender_run, tmp_path
):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    jm = JobManager(
        assets=[_entry("A")],
        blender_exe="fake-blender",
        output_dir=str(output_dir),
        addon_name="addon.x",
    )

    started: list[tuple[int, str]] = []
    completed: list[tuple[int, str, bool]] = []
    jm.job_started.connect(lambda i, n: started.append((i, n)))
    jm.job_completed.connect(lambda i, n, s: completed.append((i, n, s)))

    _start_and_wait(qtbot, jm)
    assert started == [(0, "A")]
    assert completed == [(0, "A", True)]


def test_blender_exe_passed_through_to_runner(qtbot, mock_blender_run, tmp_path):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    jm = JobManager(
        assets=[_entry("X")],
        blender_exe=r"C:\fake\blender.exe",
        output_dir=str(output_dir),
        addon_name="addon.x",
        timeout=42,
    )
    _start_and_wait(qtbot, jm)
    call = mock_blender_run["calls"][0]
    assert call["blender_exe"] == r"C:\fake\blender.exe"
    assert call["timeout"] == 42
    assert call["manifest"]["addon_name"] == "addon.x"


def test_cancel_stops_after_current_asset(qtbot, mock_blender_run, tmp_path):
    """Calling cancel() before the loop iterates should mark items as cancelled."""
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    jm = JobManager(
        assets=[_entry("A"), _entry("B"), _entry("C")],
        blender_exe="fake-blender",
        output_dir=str(output_dir),
        addon_name="addon.x",
    )
    jm.cancel()  # cancel before run starts
    _start_and_wait(qtbot, jm)
    # No assets should have been processed
    assert len(jm.results) == 0


# ---------------------------------------------------------------------------
# Phase 2.6: asset_updated signal + Phase 2.7: cancel_check plumbing
# ---------------------------------------------------------------------------

def test_asset_updated_signal_emits_state_dict_on_success(
    qtbot, mock_blender_run, tmp_path
):
    """On a successful run the worker thread should emit asset_updated
    instead of relying solely on direct AssetEntry mutation."""
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    jm = JobManager(
        assets=[_entry("A")],
        blender_exe="fake-blender",
        output_dir=str(output_dir),
        addon_name="addon.x",
    )
    received: list[tuple[int, dict]] = []
    jm.asset_updated.connect(lambda i, s: received.append((i, dict(s))))
    _start_and_wait(qtbot, jm)
    assert len(received) == 1
    idx, state = received[0]
    assert idx == 0
    assert state["processed"] is True
    assert state["blend_path"].name == "A.blend"


def test_run_blender_called_with_cancel_check(qtbot, mock_blender_run, tmp_path):
    """JobManager should pass a `cancel_check` callable into run_blender."""
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    jm = JobManager(
        assets=[_entry("A")],
        blender_exe="fake-blender",
        output_dir=str(output_dir),
        addon_name="addon.x",
    )
    _start_and_wait(qtbot, jm)
    call = mock_blender_run["calls"][0]
    assert callable(call["cancel_check"])
    assert callable(call["on_proc_started"])
    # Initially not cancelled.
    assert call["cancel_check"]() is False
