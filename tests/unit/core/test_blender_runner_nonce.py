"""Tests for the per-run nonce protocol in `core.blender_runner.run_blender`.

The runner generates a fresh ``secrets.token_hex(8)`` per call, passes it
to Blender via ``--nonce <hex>``, and then only trusts stdout lines whose
status prefix carries that exact nonce.  This file exercises the parser
half of that contract by stubbing ``subprocess.Popen`` to emit canned
stdout for the run.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest

import core.blender_runner as br
from core.blender_runner import reset_blender_validation_cache, run_blender

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_validation_cache():
    reset_blender_validation_cache()
    yield
    reset_blender_validation_cache()


def _make_blender_exe(tmp_path):
    p = tmp_path / "blender.exe"
    p.write_bytes(b"\x7fELF" + b"x" * 32)
    return p


def _stub_validate_passes(monkeypatch):
    """Make `_validate_blender_exe` return success without launching anything."""

    def _fake_run(cmd, **kwargs):
        result = MagicMock()
        result.stdout = "Blender 4.2.0\n"
        return result

    monkeypatch.setattr(br.subprocess, "run", _fake_run)


def _good_manifest(tmp_path):
    return {
        "psk_path": "C:/games/foo/Mesh.psk",
        "output_path": str(tmp_path / "out.blend"),
        "materials": {},
    }


class _FakePopen:
    """Stand-in for `subprocess.Popen` that returns canned stdout/stderr."""

    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.received_cmd: list[str] = []

    def communicate(self, timeout=None):
        return (self._stdout, self._stderr)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass

    def terminate(self):
        pass


def test_run_blender_passes_nonce_to_subprocess(monkeypatch, tmp_path):
    _stub_validate_passes(monkeypatch)
    p = _make_blender_exe(tmp_path)
    captured: dict = {}

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _FakePopen(stdout="", returncode=0)

    monkeypatch.setattr(br.subprocess, "Popen", _fake_popen)

    run_blender(blender_exe=str(p), manifest=_good_manifest(tmp_path))

    assert "--nonce" in captured["cmd"]
    nonce_idx = captured["cmd"].index("--nonce")
    nonce = captured["cmd"][nonce_idx + 1]
    # secrets.token_hex(8) -> 16 hex chars
    assert len(nonce) == 16 and all(c in "0123456789abcdef" for c in nonce)


def test_run_blender_parses_nonce_prefixed_status(monkeypatch, tmp_path):
    """Status lines carrying the active nonce update the result."""
    _stub_validate_passes(monkeypatch)
    p = _make_blender_exe(tmp_path)
    seen_status: list[dict] = []

    def _fake_popen(cmd, **kwargs):
        nonce_idx = list(cmd).index("--nonce")
        nonce = cmd[nonce_idx + 1]
        completed = {
            "status": "completed",
            "materials_processed": 4,
            "materials_failed": 1,
        }
        out = f"##ASSET_STATUS:{nonce}##{json.dumps(completed)}\n"
        return _FakePopen(stdout=out, returncode=0)

    monkeypatch.setattr(br.subprocess, "Popen", _fake_popen)

    result = run_blender(
        blender_exe=str(p),
        manifest=_good_manifest(tmp_path),
        on_status=lambda s: seen_status.append(s),
    )
    assert result.success is True
    assert result.materials_processed == 4
    assert result.materials_failed == 1
    assert seen_status and seen_status[0]["status"] == "completed"


def test_run_blender_ignores_status_with_wrong_nonce(monkeypatch, tmp_path):
    """A spoofed status line with a *different* nonce must be ignored."""
    _stub_validate_passes(monkeypatch)
    p = _make_blender_exe(tmp_path)

    def _fake_popen(cmd, **kwargs):
        # Emit a status line carrying a *different* nonce — pretend
        # something on stdout is trying to spoof the wire format.
        bogus = {"status": "error", "message": "ATTACK"}
        out = f"##ASSET_STATUS:deadbeefdeadbeef##{json.dumps(bogus)}\n"
        return _FakePopen(stdout=out, returncode=0)

    monkeypatch.setattr(br.subprocess, "Popen", _fake_popen)

    result = run_blender(blender_exe=str(p), manifest=_good_manifest(tmp_path))
    # The bogus error message must not have been adopted.
    assert "ATTACK" not in result.error_message


def test_run_blender_still_accepts_legacy_unprefixed_status(monkeypatch, tmp_path):
    """Legacy ``##ASSET_STATUS##`` (no nonce) is still parsed for back-compat."""
    _stub_validate_passes(monkeypatch)
    p = _make_blender_exe(tmp_path)

    def _fake_popen(cmd, **kwargs):
        completed = {"status": "completed", "materials_processed": 2}
        out = f"##ASSET_STATUS##{json.dumps(completed)}\n"
        return _FakePopen(stdout=out, returncode=0)

    monkeypatch.setattr(br.subprocess, "Popen", _fake_popen)

    result = run_blender(blender_exe=str(p), manifest=_good_manifest(tmp_path))
    assert result.materials_processed == 2


def test_run_blender_short_circuits_on_invalid_manifest(monkeypatch, tmp_path):
    """If the manifest fails the runner allowlist, Popen must not be called."""
    _stub_validate_passes(monkeypatch)
    p = _make_blender_exe(tmp_path)

    popen_calls = {"n": 0}

    def _fake_popen(*args, **kwargs):
        popen_calls["n"] += 1
        return _FakePopen("", returncode=0)

    monkeypatch.setattr(br.subprocess, "Popen", _fake_popen)

    bad = {"psk_path": "C:/x/y.psk", "output_path": "relative.blend"}
    result = run_blender(blender_exe=str(p), manifest=bad)
    assert result.success is False
    assert "manifest validation" in result.error_message.lower()
    assert popen_calls["n"] == 0
