"""QThread-based job queue for batch processing assets through Blender.

Runs one Blender job at a time, emits signals for GUI updates,
writes a log file, and supports cancellation.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from _base import base_dir
from core.asset_scanner import AssetEntry
from core.blender_runner import BlenderResult, run_blender
from core.log_redaction import redact_sensitive
from core import queue_checkpoint

log = logging.getLogger(__name__)


class JobManager(QThread):
    """Processes a queue of assets sequentially through Blender."""

    # Signals
    job_started = Signal(int, str)          # (index, asset_name)
    job_completed = Signal(int, str, bool)  # (index, asset_name, success)
    job_progress = Signal(int, str, str)    # (index, asset_name, step)
    queue_finished = Signal(int, int, int)  # (total, succeeded, failed)
    log_message = Signal(str, str)          # (message, level: info/warning/error/success)
    # Emitted when an asset's processed-state changes mid-batch. The receiver
    # is expected to apply the mutation on the GUI thread (and notify any
    # connected QAbstractItemModel via dataChanged), instead of relying on
    # the worker thread mutating shared AssetEntry instances directly.
    asset_updated = Signal(int, object)     # (index, state_dict)

    def __init__(
        self,
        assets: list[AssetEntry],
        blender_exe: str,
        output_dir: str,
        addon_name: str,
        timeout: int = 120,
        parent=None,
        *,
        profile_name: str = "",
        already_completed: Optional[list[str]] = None,
    ):
        super().__init__(parent)
        self._assets = list(assets)
        self._blender_exe = blender_exe
        self._output_dir = output_dir
        self._addon_name = addon_name
        self._timeout = timeout
        self._cancelled = False
        self._results: list[BlenderResult] = []
        # Profile name + already-completed psk_path list go into the queue
        # checkpoint so a resumed batch picks up where the previous run
        # left off without losing identity.
        self._profile_name = profile_name
        self._completed_paths: list[str] = list(already_completed or [])
        # Tracks the currently-running Blender subprocess so cancel() can
        # terminate it directly instead of letting the timeout run out.
        self._active_proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()

    def cancel(self):
        self._cancelled = True
        # If there's a live subprocess, kick it now — otherwise cancel only
        # takes effect at the next loop boundary, which can be many minutes
        # away when timeouts are large.
        with self._proc_lock:
            proc = self._active_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    @property
    def results(self) -> list[BlenderResult]:
        return list(self._results)

    def run(self):
        total = len(self._assets)
        succeeded = 0
        failed = 0

        # Set up log file under the install-rooted logs/ dir (avoids surprising
        # log placement when the user picks an output_dir on a network share or
        # at a drive root).
        log_dir = base_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"batch_{datetime.now():%Y%m%d_%H%M%S}.log"

        self.log_message.emit(
            f"Starting batch: {total} assets → {self._output_dir}", "info"
        )

        # Initial checkpoint write — gives us a recovery anchor even if the
        # very first asset crashes Blender hard.
        self._write_checkpoint()

        for idx, asset in enumerate(self._assets):
            if self._cancelled:
                self.log_message.emit("Batch cancelled by user", "warning")
                break

            asset_name = asset.name
            self.job_started.emit(idx, asset_name)
            self.log_message.emit(
                f"[{idx + 1}/{total}] Processing: {asset_name}", "info"
            )

            # Build output path
            output_path = str(
                Path(self._output_dir) / f"{asset_name}.blend"
            )

            # Build manifest
            manifest = asset.to_manifest(
                output_path=Path(output_path),
                addon_name=self._addon_name,
            )

            # Status callback for real-time updates
            def on_status(status, _idx=idx, _name=asset_name):
                step = status.get("step", status.get("status", ""))
                self.job_progress.emit(_idx, _name, step)

            # Track the live subprocess so cancel() can terminate it directly.
            def on_proc_started(proc, _self=self):
                with _self._proc_lock:
                    _self._active_proc = proc

            # Run Blender
            try:
                result = run_blender(
                    blender_exe=self._blender_exe,
                    manifest=manifest,
                    timeout=self._timeout,
                    on_status=on_status,
                    cancel_check=lambda: self._cancelled,
                    on_proc_started=on_proc_started,
                )
            finally:
                with self._proc_lock:
                    self._active_proc = None

            self._results.append(result)

            if result.success:
                succeeded += 1
                # Defer the AssetEntry mutation to the GUI thread via signal —
                # the worker thread should not be writing to shared model
                # state directly. We still fall back to writing here so
                # callers that don't connect asset_updated keep working.
                state = {
                    "blend_path": Path(output_path),
                    "processed": True,
                }
                self.asset_updated.emit(idx, state)
                asset.blend_path = Path(output_path)
                asset.processed = True
                self.log_message.emit(
                    f"  ✓ {asset_name}: {result.materials_processed} materials wired",
                    "success",
                )
                if result.warnings:
                    for w in result.warnings:
                        self.log_message.emit(f"    ⚠ {w}", "warning")
            else:
                failed += 1
                reason = result.error_message or "Unknown failure"
                if result.timed_out:
                    reason = f"Timed out after {self._timeout}s"
                self.log_message.emit(
                    f"  ✗ {asset_name}: SKIPPED — {reason}", "error"
                )

            self.job_completed.emit(idx, asset_name, result.success)

            # Write to log file
            self._write_log_entry(log_path, idx, total, asset, result)

            # Record in checkpoint regardless of success/failure — a resumed
            # batch should pick up at the next un-touched asset, not retry
            # something that already produced a (possibly broken) output.
            self._completed_paths.append(str(asset.psk_path))
            self._write_checkpoint()

        # Clean exit (not cancelled) drops the checkpoint so it doesn't
        # haunt the next launch. A cancelled batch leaves it in place so
        # the user can resume from where they stopped.
        if not self._cancelled:
            try:
                queue_checkpoint.delete()
            except Exception:
                log.exception("Failed to delete queue checkpoint after clean finish")

        self.log_message.emit(
            f"Batch complete: {succeeded} succeeded, {failed} failed, "
            f"{total - succeeded - failed} cancelled",
            "info",
        )
        self.log_message.emit(f"Log saved: {log_path}", "info")
        self.queue_finished.emit(total, succeeded, failed)

    def _write_checkpoint(self) -> None:
        """Persist current pending + completed lists. Best-effort: a failed
        write logs and continues so the batch isn't stalled by a bad disk."""
        try:
            queue_checkpoint.save(
                profile=self._profile_name,
                pending=self._assets,
                completed=self._completed_paths,
            )
        except Exception:
            log.exception("Failed to write queue checkpoint")

    def _write_log_entry(
        self,
        log_path: Path,
        idx: int,
        total: int,
        asset: AssetEntry,
        result: BlenderResult,
    ):
        entry = {
            "index": idx + 1,
            "total": total,
            "asset": asset.name,
            "psk_path": str(asset.psk_path),
            "success": result.success,
            "materials_processed": result.materials_processed,
            "materials_failed": result.materials_failed,
            "error": result.error_message if not result.success else None,
            "warnings": result.warnings,
            "timed_out": result.timed_out,
            "return_code": result.return_code,
            "timestamp": datetime.now().isoformat(),
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(redact_sensitive(entry)) + "\n")
