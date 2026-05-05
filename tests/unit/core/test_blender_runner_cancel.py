"""Unit tests for the `cancel_check` plumbing in `run_blender`."""

from __future__ import annotations

import subprocess
import threading

import pytest

from core.blender_runner import _wait_with_cancel

pytestmark = pytest.mark.unit


class _FakeProc:
    """Minimal Popen stand-in for `_wait_with_cancel`.

    ``communicate()`` blocks until the process is signalled via
    ``terminate()`` or ``kill()`` (or pre-exits when ``auto_exit=True``).
    This mirrors real Popen behaviour where communicate() drains pipes and
    only returns after the subprocess exits.
    """

    def __init__(self, auto_exit: bool = False, returncode: int = 0):
        self._exit_event = threading.Event()
        self._final_rc = returncode
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.communicated = False
        if auto_exit:
            self.returncode = returncode
            self._exit_event.set()

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self._exit_event.set()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._exit_event.set()

    def communicate(self, timeout=None):
        self.communicated = True
        if timeout is not None:
            if not self._exit_event.wait(timeout=timeout):
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        else:
            self._exit_event.wait()
        if self.returncode is None:
            self.returncode = self._final_rc
        return ("stdout text", "stderr text")


def test_wait_with_cancel_no_callback_uses_simple_path():
    """Without `cancel_check`, behaviour matches the old `proc.communicate`."""
    proc = _FakeProc(auto_exit=True, returncode=0)
    cancelled, stdout, stderr = _wait_with_cancel(proc, timeout=10, cancel_check=None)
    assert cancelled is False
    assert stdout == "stdout text"
    assert stderr == "stderr text"


def test_wait_with_cancel_returns_cancelled_when_check_returns_true():
    """When `cancel_check()` flips True, the proc is terminated and we return."""
    proc = _FakeProc()  # never naturally exits
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
    proc = _FakeProc()  # never naturally exits

    def never_cancel():
        return False

    cancelled, stdout, stderr = _wait_with_cancel(
        proc, timeout=0, cancel_check=never_cancel
    )
    assert cancelled is False
    assert stdout is None  # signals timeout
    assert proc.killed is True
