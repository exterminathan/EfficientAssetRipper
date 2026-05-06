"""Qt-tier fixtures (require pytest-qt)."""

from __future__ import annotations

import os

import pytest

# Must be set before QApplication is created (pytest-qt creates it lazily on
# first qtbot use). Suppresses real windows during headless CI/build runs.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp_args():
    """pytest-qt hook: pass a name so QApplication.instance() is identifiable."""
    return ["EAR-tests"]


@pytest.fixture(autouse=True)
def _isolate_queue_checkpoint(tmp_path, monkeypatch):
    """Redirect ``core.queue_checkpoint`` to ``tmp_path`` for every Qt test.

    JobManager writes a checkpoint after every job, so without isolation a
    test that exercises the runner would scribble on the real
    ``cache/queue_checkpoint.json`` and bleed state across runs.
    """
    from core import queue_checkpoint
    monkeypatch.setattr(
        queue_checkpoint,
        "_DEFAULT_PATH",
        tmp_path / "queue_checkpoint.json",
    )


@pytest.fixture
def stubbed_main_window(qtbot, mock_qsettings, mock_blender_run, monkeypatch):
    """Construct MainWindow with all external SDKs / subprocess stubbed.

    Useful for menu/window-shell smoke tests. The window is registered with
    qtbot for cleanup but not shown.
    """
    from tests.conftest import FakeEverythingSDK
    import core.everything as everything

    fake = FakeEverythingSDK()
    monkeypatch.setattr(everything, "_instance", fake)
    monkeypatch.setattr(everything, "get_sdk", lambda dll_path=None: fake)

    # Avoid touching the user's real registry-derived settings on construct
    from gui.main_window import MainWindow
    win = MainWindow()
    qtbot.addWidget(win)
    return win
