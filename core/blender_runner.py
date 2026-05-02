"""Launch Blender as a subprocess and monitor its output.

Handles timeouts, crash detection, and structured status parsing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from _base import base_dir
from core.log_redaction import redact_sensitive

log = logging.getLogger(__name__)

BLENDER_SCRIPT = str(
    base_dir() / "blender" / "process_asset.py"
)


# In-memory registry of validated Blender binaries. Keyed by absolute path,
# value is a (mtime_ns, sha256_prefix) tuple. Re-validation runs whenever the
# path or mtime drifts — typical sessions hit the cache exactly once.
_BLENDER_VALIDATION_CACHE: dict[str, tuple[int, str, str]] = {}


class BlenderIdentityError(RuntimeError):
    """Raised when a configured Blender path does not look like Blender."""


def _hash_prefix(path: Path, *, n: int = 4096) -> str:
    """Return the SHA-256 of the first *n* bytes of *path* (cheap fingerprint)."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(n)
    except OSError:
        return ""
    return hashlib.sha256(chunk).hexdigest()


def _validate_blender_exe(blender_exe: str) -> tuple[bool, str]:
    """Verify *blender_exe* actually runs and self-identifies as Blender.

    Returns ``(ok, message)``. ``ok=False`` means the binary did not produce
    a ``Blender`` version banner — either because it's not Blender, the
    file is corrupt, or the launch flat-out failed.

    Result is cached per absolute path until either path or mtime changes,
    so the version probe runs at most once per session per binary.
    """
    if not blender_exe:
        return False, "Blender executable path is empty"

    abs_path = os.path.abspath(blender_exe)
    p = Path(abs_path)
    if not p.is_file():
        return False, f"Blender executable not found: {abs_path}"

    try:
        mtime = p.stat().st_mtime_ns
    except OSError as e:
        return False, f"Could not stat Blender executable: {e}"

    fingerprint = _hash_prefix(p)
    cached = _BLENDER_VALIDATION_CACHE.get(abs_path)
    if cached is not None and cached[0] == mtime and cached[1] == fingerprint:
        return True, cached[2]

    try:
        result = subprocess.run(
            [abs_path, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"Failed to launch Blender for --version: {e}"

    banner = (result.stdout or "").strip().splitlines()[:1]
    banner_line = banner[0] if banner else ""
    if not banner_line.startswith("Blender "):
        return False, (
            f"Executable did not self-identify as Blender (stdout starts: "
            f"{banner_line[:80]!r})"
        )

    _BLENDER_VALIDATION_CACHE[abs_path] = (mtime, fingerprint, banner_line)
    return True, banner_line


def reset_blender_validation_cache() -> None:
    """Clear the per-session validation cache (test helper)."""
    _BLENDER_VALIDATION_CACHE.clear()


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
    cancel_check: Optional[Callable[[], bool]] = None,
    on_proc_started: Optional[Callable[["subprocess.Popen"], None]] = None,
) -> BlenderResult:
    """Run Blender in background mode to process a single asset.

    Args:
        blender_exe: Path to blender executable
        manifest: Job manifest dict (will be written to temp JSON file)
        timeout: Max seconds to wait
        on_status: Optional callback for real-time status updates
        cancel_check: Optional zero-arg callable that returns True when the
            caller wants this run aborted. Polled while we wait on Blender;
            on True we terminate the subprocess and return early instead of
            letting the timeout run out.
        on_proc_started: Optional callback invoked once with the live
            ``subprocess.Popen`` so the caller can hold a reference and call
            ``terminate()``/``kill()`` directly if needed.
    """
    asset_name = os.path.basename(manifest.get("psk_path", "unknown"))
    result = BlenderResult(success=False, asset_name=asset_name)

    # First-use-per-session identity check on the configured Blender binary.
    # Skipping this would let any subprocess silently masquerade as Blender.
    ok, banner = _validate_blender_exe(blender_exe)
    if not ok:
        result.error_message = f"Blender identity check failed: {banner}"
        log.error(result.error_message)
        return result

    # Write manifest under cache/manifests/ so app uninstall sweeps it
    # (system temp leaks cruft when sessions don't shut down cleanly).
    manifests_dir = base_dir() / "cache" / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".json", prefix="ear_manifest_", dir=str(manifests_dir)
    )
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

        # Sanitize cmd for logging — no secrets currently flow through argv,
        # but the redactor is cheap and a future change might.
        log.info("Launching Blender: %s", " ".join(redact_sensitive(cmd)))

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        if on_proc_started is not None:
            try:
                on_proc_started(proc)
            except Exception:
                log.exception("on_proc_started callback raised — ignoring")

        cancelled, stdout, stderr = _wait_with_cancel(
            proc, timeout=timeout, cancel_check=cancel_check
        )
        if cancelled:
            result.return_code = proc.returncode if proc.returncode is not None else -1
            result.error_message = "Cancelled by user"
            result.stdout = stdout
            result.stderr = stderr
            log.info("Blender cancelled for %s", asset_name)
            return result
        if stdout is None:
            # _wait_with_cancel signals timeout by returning (False, None, ...)
            result.timed_out = True
            result.error_message = f"Blender timed out after {timeout}s"
            result.stderr = stderr or ""
            result.return_code = -1
            log.error("Blender timed out for %s", asset_name)
            return result

        result.stdout = stdout
        result.stderr = stderr or ""
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
            # Try to extract useful info from stderr (redacted in case the
            # subprocess echoed any sensitive command-line state).
            if stderr.strip():
                last_lines = stderr.strip().splitlines()[-5:]
                result.error_message += "\n" + redact_sensitive("\n".join(last_lines))

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


def _wait_with_cancel(
    proc: "subprocess.Popen",
    timeout: int,
    cancel_check: Optional[Callable[[], bool]],
) -> tuple[bool, Optional[str], Optional[str]]:
    """Wait for *proc* to exit, polling ``cancel_check`` along the way.

    Returns ``(cancelled, stdout, stderr)``:

    - ``cancelled=True`` — caller asked us to stop; subprocess has been
      terminated/killed and pipes drained.
    - ``cancelled=False, stdout=None`` — timeout elapsed; subprocess killed,
      stderr is whatever drained.
    - ``cancelled=False, stdout=str`` — normal exit; ``stdout``/``stderr``
      are the full captured streams.
    """
    # Without a cancel callback the old simple path is still ideal — we don't
    # need to spin in a poll loop for the common case.
    if cancel_check is None:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate()
            return False, None, stderr
        return False, stdout, stderr

    poll_interval = 0.25
    elapsed = 0.0
    while True:
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            return False, stdout, stderr
        if cancel_check():
            stdout, stderr = _terminate_then_kill(proc)
            return True, stdout, stderr

        # Time-based timeout independent of clock skew.
        if elapsed >= timeout:
            proc.kill()
            try:
                _, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stderr = ""
            return False, None, stderr

        try:
            proc.wait(timeout=poll_interval)
        except subprocess.TimeoutExpired:
            elapsed += poll_interval
            continue
        # If wait returned, the next loop iteration handles the exit.


def _terminate_then_kill(proc: "subprocess.Popen") -> tuple[str, str]:
    """Politely terminate, then kill after a 5s grace. Returns (stdout, stderr)."""
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        return proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            return proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            return "", ""


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
