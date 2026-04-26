"""Tests for `core.unpacker.UnpackerProcess` with a stubbed QProcess.

We don't launch a real CUE4ParseCLI here. Instead we replace the internal
QProcess instance with a stub that lets the test feed bytes into _on_stdout
and assert which signal got emitted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from PySide6.QtCore import QByteArray, QObject, QProcess, Signal

from core.unpacker import UnpackerProcess

pytestmark = pytest.mark.qt


# ---------------------------------------------------------------------------
# Stub QProcess (just enough for the readyReadStandardOutput pathway)
# ---------------------------------------------------------------------------

class StubQProcess(QObject):
    readyReadStandardOutput = Signal()
    readyReadStandardError = Signal()
    finished = Signal(int, QProcess.ExitStatus)
    errorOccurred = Signal(QProcess.ProcessError)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buf = bytearray()
        self._err = bytearray()
        self._state = QProcess.ProcessState.Running
        self.write_log: list[bytes] = []

    def state(self):
        return self._state

    def setProgram(self, *_):
        pass

    def setProcessChannelMode(self, *_):
        pass

    def waitForStarted(self, _ms):
        return True

    def waitForFinished(self, _ms):
        return True

    def kill(self):
        self._state = QProcess.ProcessState.NotRunning

    def write(self, data: bytes) -> int:
        self.write_log.append(bytes(data))
        return len(data)

    def readAllStandardOutput(self) -> QByteArray:
        ba = QByteArray(bytes(self._buf))
        self._buf.clear()
        return ba

    def readAllStandardError(self) -> QByteArray:
        ba = QByteArray(bytes(self._err))
        self._err.clear()
        return ba

    # Test-only helpers
    def feed(self, line: str) -> None:
        if not line.endswith("\n"):
            line += "\n"
        self._buf.extend(line.encode("utf-8"))
        self.readyReadStandardOutput.emit()

    def feed_chunk(self, raw: bytes) -> None:
        self._buf.extend(raw)
        self.readyReadStandardOutput.emit()


# ---------------------------------------------------------------------------
# Fixture: pre-built unpacker with a stub process attached
# ---------------------------------------------------------------------------

@pytest.fixture
def unpacker_with_stub(qtbot):
    up = UnpackerProcess()
    qtbot.add_widget = qtbot.addWidget  # alias for safety
    proc = StubQProcess(up)
    proc.readyReadStandardOutput.connect(up._on_stdout)
    proc.readyReadStandardError.connect(up._on_stderr)
    up._proc = proc
    return up, proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_initialized_signal_fires_on_init_ndjson(unpacker_with_stub, qtbot):
    up, proc = unpacker_with_stub
    msg = {
        "type": "init_done",
        "archive_count": 3,
        "unmounted_count": 1,
        "file_count": 100,
        "keys_submitted": 2,
        "loose_file_count": 5,
    }
    with qtbot.waitSignal(up.initialized, timeout=1000) as sig:
        proc.feed(json.dumps(msg))
    assert sig.args == [3, 1, 100, 2, 5]


def test_browse_result_signal_carries_entries(unpacker_with_stub, qtbot):
    up, proc = unpacker_with_stub
    payload = {
        "type": "browse_result",
        "path": "/Game/Foo",
        "entries": [{"name": "Bar", "is_folder": True}],
    }
    with qtbot.waitSignal(up.browse_result, timeout=1000) as sig:
        proc.feed(json.dumps(payload))
    assert sig.args[0] == "/Game/Foo"
    assert sig.args[1] == [{"name": "Bar", "is_folder": True}]


def test_export_done_emitted_with_succeeded_and_failed_lists(unpacker_with_stub, qtbot):
    up, proc = unpacker_with_stub
    payload = {
        "type": "export_done",
        "succeeded": ["/Game/A.uasset", "/Game/B.uasset"],
        "failed": [{"path": "/Game/C.uasset", "reason": "missing key"}],
    }
    with qtbot.waitSignal(up.export_done, timeout=1000) as sig:
        proc.feed(json.dumps(payload))
    assert sig.args[0] == ["/Game/A.uasset", "/Game/B.uasset"]
    assert sig.args[1][0]["reason"] == "missing key"


def test_progress_signal_emitted(unpacker_with_stub, qtbot):
    up, proc = unpacker_with_stub
    payload = {"type": "progress", "current": 7, "total": 100, "message": "halfway"}
    with qtbot.waitSignal(up.progress, timeout=1000) as sig:
        proc.feed(json.dumps(payload))
    assert sig.args == [7, 100, "halfway"]


def test_partial_line_buffered_until_newline(unpacker_with_stub, qtbot):
    """A JSON line split across two reads should not be parsed prematurely."""
    up, proc = unpacker_with_stub
    payload = {"type": "warning", "message": "split-line warning"}
    raw = (json.dumps(payload) + "\n").encode("utf-8")
    half = len(raw) // 2

    # First half should not trigger a signal
    with qtbot.assertNotEmitted(up.warning, wait=200):
        proc.feed_chunk(raw[:half])

    # Second half completes the line — signal fires
    with qtbot.waitSignal(up.warning, timeout=1000) as sig:
        proc.feed_chunk(raw[half:])
    assert sig.args[0] == "split-line warning"


def test_invalid_ndjson_emits_no_signal_no_crash(unpacker_with_stub, qtbot):
    """Garbage stdout from CLI should not crash the parser."""
    up, proc = unpacker_with_stub
    with qtbot.assertNotEmitted(up.error, wait=200):
        proc.feed("this is not json at all")


def test_unknown_message_type_logged_not_signaled(unpacker_with_stub, qtbot):
    up, proc = unpacker_with_stub
    with qtbot.assertNotEmitted(up.warning, wait=200):
        proc.feed(json.dumps({"type": "totally_unknown_type", "x": 1}))


def test_send_writes_json_with_newline(unpacker_with_stub):
    up, proc = unpacker_with_stub
    up.browse("/Game/X")
    assert proc.write_log
    payload = proc.write_log[0]
    assert payload.endswith(b"\n")
    parsed = json.loads(payload.decode("utf-8").strip())
    assert parsed == {"cmd": "browse", "path": "/Game/X"}


def test_send_when_not_running_emits_error(qtbot):
    """When _proc is None, sending should emit an error signal, not crash."""
    up = UnpackerProcess()
    with qtbot.waitSignal(up.error, timeout=1000) as sig:
        up.browse("/X")
    assert "not running" in sig.args[0].lower()


def test_warning_message_signal(unpacker_with_stub, qtbot):
    up, proc = unpacker_with_stub
    with qtbot.waitSignal(up.warning, timeout=1000) as sig:
        proc.feed(json.dumps({"type": "warning", "message": "watch out"}))
    assert sig.args[0] == "watch out"


def test_error_message_signal(unpacker_with_stub, qtbot):
    up, proc = unpacker_with_stub
    with qtbot.waitSignal(up.error, timeout=1000) as sig:
        proc.feed(json.dumps({"type": "error", "message": "boom"}))
    assert sig.args[0] == "boom"
