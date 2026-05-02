"""Tests for `core.blender_runner._validate_blender_exe`.

The validator launches the candidate binary with ``--version`` and
inspects stdout. Tests stub :mod:`subprocess` at the module level so no
real process is spawned.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

import core.blender_runner as br
from core.blender_runner import (
    _validate_blender_exe,
    reset_blender_validation_cache,
    run_blender,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_validation_cache():
    reset_blender_validation_cache()
    yield
    reset_blender_validation_cache()


def _make_blender_exe(tmp_path):
    """Create a real (empty) file that os.path.isfile and stat will accept."""
    p = tmp_path / "blender.exe"
    p.write_bytes(b"\x7fELF" + b"x" * 32)
    return p


def test_validate_returns_false_for_missing_binary(tmp_path):
    ok, msg = _validate_blender_exe(str(tmp_path / "nope.exe"))
    assert ok is False
    assert "not found" in msg.lower()


def test_validate_returns_false_for_empty_path():
    ok, msg = _validate_blender_exe("")
    assert ok is False


def test_validate_succeeds_when_subprocess_returns_blender_banner(monkeypatch, tmp_path):
    p = _make_blender_exe(tmp_path)

    def _fake_run(cmd, **kwargs):
        result = MagicMock()
        result.stdout = "Blender 4.2.0\n"
        return result

    monkeypatch.setattr(br.subprocess, "run", _fake_run)
    ok, msg = _validate_blender_exe(str(p))
    assert ok is True
    assert msg.startswith("Blender ")


def test_validate_rejects_non_blender_banner(monkeypatch, tmp_path):
    p = _make_blender_exe(tmp_path)

    def _fake_run(cmd, **kwargs):
        result = MagicMock()
        result.stdout = "evil-impostor v1.2\n"
        return result

    monkeypatch.setattr(br.subprocess, "run", _fake_run)
    ok, msg = _validate_blender_exe(str(p))
    assert ok is False
    assert "self-identify" in msg.lower()


def test_validate_caches_result(monkeypatch, tmp_path):
    p = _make_blender_exe(tmp_path)
    counter = {"n": 0}

    def _fake_run(cmd, **kwargs):
        counter["n"] += 1
        result = MagicMock()
        result.stdout = "Blender 4.2.0\n"
        return result

    monkeypatch.setattr(br.subprocess, "run", _fake_run)
    _validate_blender_exe(str(p))
    _validate_blender_exe(str(p))
    assert counter["n"] == 1, "validator should not re-launch on repeated calls"


def test_validate_re_runs_when_mtime_changes(monkeypatch, tmp_path):
    p = _make_blender_exe(tmp_path)
    counter = {"n": 0}

    def _fake_run(cmd, **kwargs):
        counter["n"] += 1
        result = MagicMock()
        result.stdout = "Blender 4.2.0\n"
        return result

    monkeypatch.setattr(br.subprocess, "run", _fake_run)
    _validate_blender_exe(str(p))
    # Touch the file so the cached mtime no longer matches.
    p.write_bytes(p.read_bytes() + b"\x00")
    _validate_blender_exe(str(p))
    assert counter["n"] == 2


def test_validate_returns_false_when_subprocess_raises(monkeypatch, tmp_path):
    p = _make_blender_exe(tmp_path)

    def _raise(*a, **k):
        raise OSError("denied")

    monkeypatch.setattr(br.subprocess, "run", _raise)
    ok, msg = _validate_blender_exe(str(p))
    assert ok is False
    assert "failed to launch" in msg.lower()


def test_run_blender_short_circuits_when_identity_check_fails(monkeypatch, tmp_path):
    """If the binary doesn't self-identify as Blender, run_blender must bail
    before spawning the long-running render subprocess."""
    p = _make_blender_exe(tmp_path)

    def _fake_run(cmd, **kwargs):
        result = MagicMock()
        result.stdout = "definitely-not-blender\n"
        return result

    popen_called = {"n": 0}

    def _fake_popen(*a, **k):
        popen_called["n"] += 1
        raise AssertionError("Popen must not be called when identity check fails")

    monkeypatch.setattr(br.subprocess, "run", _fake_run)
    monkeypatch.setattr(br.subprocess, "Popen", _fake_popen)

    result = run_blender(blender_exe=str(p), manifest={"psk_path": "x.psk"})
    assert result.success is False
    assert "identity check" in result.error_message.lower()
    assert popen_called["n"] == 0
