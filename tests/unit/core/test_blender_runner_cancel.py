"""Unit tests for the `cancel_check` plumbing in `run_blender`."""

from __future__ import annotations

import pytest

from core.blender_runner import _wait_with_cancel

pytestmark = pytest.mark.unit


class _FakeProc:
    """Minimal Popen stand-in for `_wait_with_cancel`."""

    def __init__(self, exit_after_calls: int = 9999, returncode: int = 0):
        self._poll_count = 0
        self._exit_after = exit_after_calls
        self.returncode = None
        self._final_rc = returncode
        self.terminated = False
        self.killed = False
        self.communicated = False

    def poll(self):
        self._poll_count += 1
        if self._poll_count >= self._exit_after:
            self.returncode = self._final_rc
            return self._final_rc
        return None

    def wait(self, timeout=None):
        # Behave like a child still running for the duration of `timeout`.
        import subprocess
        # Each call advances internal "ticks" toward eventual exit.
        if self._poll_count >= self._exit_after - 1:
            self.returncode = self._final_rc
            return self._final_rc
        raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.returncode = -9

    def communicate(self, timeout=None):
        self.communicated = True
        if self.returncode is None:
            self.returncode = self._final_rc
        return ("stdout text", "stderr text")


def test_wait_with_cancel_no_callback_uses_simple_path():
    """Without `cancel_check`, behaviour matches the old `proc.communicate`."""
    proc = _FakeProc(exit_after_calls=1, returncode=0)
    cancelled, stdout, stderr = _wait_with_cancel(proc, timeout=10, cancel_check=None)
    assert cancelled is False
    assert stdout == "stdout text"
    assert stderr == "stderr text"


def test_wait_with_cancel_returns_cancelled_when_check_returns_true():
    """When `cancel_check()` flips True, the proc is terminated and we return."""
    proc = _FakeProc(exit_after_calls=99999)  # never naturally exits
    flag = {"v": False}

    def check():
        # Cancel on the second poll so we exercise the in-loop cancel path
        # instead of returning immediately.
        flag["v"] = True
        return flag["v"]

    cancelled, stdout, stderr = _wait_with_cancel(
        proc, timeout=60, cancel_check=check
    )
    assert cancelled is True
    assert proc.terminated is True
    # After `terminate()`, communicate() returned the canned tuple.
    assert stdout == "stdout text"
    assert stderr == "stderr text"


def test_wait_with_cancel_returns_timeout_when_neither_exits_nor_cancels():
    """If the cancel callback always returns False and the proc never exits,
    we hit the timeout branch and signal it via stdout=None."""
    proc = _FakeProc(exit_after_calls=99999)

    def never_cancel():
        return False

    cancelled, stdout, stderr = _wait_with_cancel(
        proc, timeout=0, cancel_check=never_cancel
    )
    assert cancelled is False
    assert stdout is None  # signals timeout
    assert proc.killed is True
