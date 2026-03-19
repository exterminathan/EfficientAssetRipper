"""Launch Blender as a subprocess and monitor its output.

Handles timeouts, crash detection, and structured status parsing.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from _base import base_dir

log = logging.getLogger(__name__)

BLENDER_SCRIPT = str(
    base_dir() / "blender" / "process_asset.py"
)


@dataclass
class BlenderResult:
    success: bool
    asset_name: str = ""
    materials_processed: int = 0
    materials_failed: int = 0
    error_message: str = ""
    warnings: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    return_code: int = -1
    timed_out: bool = False


def run_blender(
    blender_exe: str,
    manifest: dict,
    timeout: int = 120,
    on_status: Optional[Callable[[dict], None]] = None,
) -> BlenderResult:
    """Run Blender in background mode to process a single asset.

    Args:
        blender_exe: Path to blender executable
        manifest: Job manifest dict (will be written to temp JSON file)
        timeout: Max seconds to wait
        on_status: Optional callback for real-time status updates
    """
    asset_name = os.path.basename(manifest.get("psk_path", "unknown"))
    result = BlenderResult(success=False, asset_name=asset_name)

    # Write manifest to temp file
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="ear_manifest_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        cmd = [
            blender_exe,
            "--background",
            "--python",
            BLENDER_SCRIPT,
            "--",
            tmp_path,
        ]

        log.info("Launching Blender: %s", " ".join(cmd))

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            result.timed_out = True
            result.error_message = f"Blender timed out after {timeout}s"
            result.stdout = stdout
            result.stderr = stderr
            result.return_code = -1
            log.error("Blender timed out for %s", asset_name)
            return result

        result.stdout = stdout
        result.stderr = stderr
        result.return_code = proc.returncode

        # Parse structured status lines from stdout
        for line in stdout.splitlines():
            if line.startswith("##ASSET_STATUS##"):
                try:
                    status = json.loads(line[len("##ASSET_STATUS##"):])
                    _process_status(status, result)
                    if on_status:
                        on_status(status)
                except json.JSONDecodeError:
                    pass

        if proc.returncode == 0 and not result.error_message:
            result.success = True
        elif not result.error_message:
            result.error_message = (
                f"Blender exited with code {proc.returncode}"
            )
            # Try to extract useful info from stderr
            if stderr.strip():
                last_lines = stderr.strip().splitlines()[-5:]
                result.error_message += "\n" + "\n".join(last_lines)

    except FileNotFoundError:
        result.error_message = f"Blender executable not found: {blender_exe}"
        log.error(result.error_message)
    except Exception as e:
        result.error_message = f"Unexpected error: {e}"
        log.error(result.error_message, exc_info=True)
    finally:
        # Clean up temp manifest
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return result


def _process_status(status: dict, result: BlenderResult):
    """Update BlenderResult from a status dict."""
    s = status.get("status", "")

    if s == "completed":
        result.materials_processed = status.get("materials_processed", 0)
        result.materials_failed = status.get("materials_failed", 0)

    elif s == "error":
        result.error_message = status.get("message", "Unknown error")

    elif s == "warning":
        result.warnings.append(status.get("message", ""))
