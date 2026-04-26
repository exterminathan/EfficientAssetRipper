"""End-to-end smoke tests against a real Blender installation.

Skipped automatically when Blender is not on PATH or BLENDER_EXE is unset.
Set BLENDER_EXE to the absolute blender.exe path to enable.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

pytestmark = [pytest.mark.requires_blender, pytest.mark.slow]


def _resolve_blender_exe() -> str:
    candidate = os.environ.get("BLENDER_EXE")
    if candidate and os.path.isfile(candidate):
        return candidate
    return shutil.which("blender") or "blender"


def test_blender_version_runs():
    """`blender --version` must return 0 and print a version line."""
    blender = _resolve_blender_exe()
    proc = subprocess.run(
        [blender, "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    assert "Blender" in proc.stdout, f"unexpected output: {proc.stdout!r}"
