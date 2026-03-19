"""QThread-based job queue for batch processing assets through Blender.

Runs one Blender job at a time, emits signals for GUI updates,
writes a log file, and supports cancellation.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from core.asset_scanner import AssetEntry
from core.blender_runner import BlenderResult, run_blender

log = logging.getLogger(__name__)


class JobManager(QThread):
    """Processes a queue of assets sequentially through Blender."""

    # Signals
    job_started = Signal(int, str)          # (index, asset_name)
    job_completed = Signal(int, str, bool)  # (index, asset_name, success)
    job_progress = Signal(int, str, str)    # (index, asset_name, step)
    queue_finished = Signal(int, int, int)  # (total, succeeded, failed)
    log_message = Signal(str, str)          # (message, level: info/warning/error/success)

    def __init__(
        self,
        assets: list[AssetEntry],
        blender_exe: str,
        output_dir: str,
        addon_name: str,
        timeout: int = 120,
        parent=None,
    ):
        super().__init__(parent)
        self._assets = list(assets)
        self._blender_exe = blender_exe
        self._output_dir = output_dir
        self._addon_name = addon_name
        self._timeout = timeout
        self._cancelled = False
        self._results: list[BlenderResult] = []

    def cancel(self):
        self._cancelled = True

    @property
    def results(self) -> list[BlenderResult]:
        return list(self._results)

    def run(self):
        total = len(self._assets)
        succeeded = 0
        failed = 0

        # Set up log file
        log_dir = Path(self._output_dir).parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"batch_{datetime.now():%Y%m%d_%H%M%S}.log"

        self.log_message.emit(
            f"Starting batch: {total} assets → {self._output_dir}", "info"
        )

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

            # Run Blender
            result = run_blender(
                blender_exe=self._blender_exe,
                manifest=manifest,
                timeout=self._timeout,
                on_status=on_status,
            )

            self._results.append(result)

            if result.success:
                succeeded += 1
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

        self.log_message.emit(
            f"Batch complete: {succeeded} succeeded, {failed} failed, "
            f"{total - succeeded - failed} cancelled",
            "info",
        )
        self.log_message.emit(f"Log saved: {log_path}", "info")
        self.queue_finished.emit(total, succeeded, failed)

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
            f.write(json.dumps(entry) + "\n")
