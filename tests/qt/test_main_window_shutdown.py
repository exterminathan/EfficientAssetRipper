"""Tests for MainWindow worker tracking + cache write helpers.

Avoids constructing live workers — uses dummy QThreads + monkeypatching.
"""

from __future__ import annotations

import pytest

from PySide6.QtCore import QThread

import config

pytestmark = [pytest.mark.qt, pytest.mark.gui]


@pytest.fixture
def main_window(qtbot, mock_qsettings, tmp_profiles_dir):
    config.set("setup_complete", "1")
    from gui.main_window import MainWindow
    win = MainWindow()
    qtbot.addWidget(win)
    return win


class _DummyWorker(QThread):
    """Bare QThread that exits immediately and reports cancellation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancel_called = False

    def cancel(self):
        self.cancel_called = True

    def run(self):
        # Exit immediately so finished/deleteLater fires.
        return


def test_track_worker_appends_and_self_prunes(main_window, qtbot):
    """A tracked worker should auto-remove itself after `finished` fires."""
    w = _DummyWorker(parent=main_window)
    main_window._track_worker(w)
    assert w in main_window._active_workers

    qtbot.waitSignal(w.finished, timeout=2000)  # arm signal listener
    w.start()
    qtbot.waitUntil(lambda: w not in main_window._active_workers, timeout=2000)


def test_cancel_active_ops_calls_each_workers_cancel(main_window):
    w1 = _DummyWorker(parent=main_window)
    w2 = _DummyWorker(parent=main_window)
    main_window._active_workers.extend([w1, w2])
    try:
        main_window._cancel_active_ops()
        assert w1.cancel_called is True
        assert w2.cancel_called is True
    finally:
        main_window._active_workers.clear()


def test_is_busy_true_when_worker_running(main_window, qtbot):
    """`_is_busy` should reflect any tracked, still-running worker."""
    class _SlowWorker(QThread):
        def cancel(self):
            self.requestInterruption()

        def run(self):
            # Sleep long enough that we can observe is_busy=True.
            self.msleep(300)

    w = _SlowWorker(parent=main_window)
    main_window._active_workers.append(w)
    w.start()
    try:
        qtbot.waitUntil(lambda: main_window._is_busy() is True, timeout=2000)
    finally:
        w.wait(2000)
    qtbot.waitUntil(lambda: main_window._is_busy() is False, timeout=2000)
    main_window._active_workers.clear()


def test_save_cache_async_submits_runnable(main_window, qtbot, monkeypatch):
    """`_save_cache_async` should bump the pending counter and dispatch a runnable."""
    from PySide6.QtCore import QThreadPool
    from core.asset_scanner import AssetEntry
    from pathlib import Path

    submitted = []

    real_start = QThreadPool.start

    def _spy(self, runnable, *a, **kw):
        submitted.append(runnable)
        return real_start(self, runnable, *a, **kw)

    monkeypatch.setattr(QThreadPool, "start", _spy)

    # Patch the actual disk-write so the runnable is harmless.
    import gui.main_window as mw
    monkeypatch.setattr(mw, "save_scan_cache", lambda *a, **kw: Path("dummy"))

    assets = [AssetEntry(psk_path=Path("/x/A.psk"), name="A")]
    main_window._save_cache_async(assets, "C:/Game")

    assert main_window._pending_cache_writes >= 1
    assert any(type(r).__name__ == "_CacheWriteRunnable" for r in submitted)


def test_force_run_setup_wizard_preserves_setup_complete(main_window, monkeypatch):
    """Help → Run Setup Wizard re-fires the wizard but must NOT clear setup_complete.

    The wizard reads ``setup_complete`` at construction to decide whether to
    show the first-run "set up your first profile" page or the re-run "go
    to the Profiles menu" page. Clearing the flag here would always look
    like a first run.
    """
    config.set("setup_complete", "1")

    constructed = []
    from gui.setup_wizard import SetupWizard
    real_init = SetupWizard.__init__

    def _spy_init(self, *a, **kw):
        constructed.append(True)
        real_init(self, *a, **kw)

    def _spy_exec(self):
        return 0  # don't actually open the modal

    monkeypatch.setattr(SetupWizard, "__init__", _spy_init)
    monkeypatch.setattr(SetupWizard, "exec", _spy_exec)

    main_window._force_run_setup_wizard()
    assert config.get("setup_complete") == "1"
    assert constructed == [True]
