"""Qt-tier fixtures (require pytest-qt)."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def qapp_args():
    """pytest-qt hook: pass a name so QApplication.instance() is identifiable."""
    return ["EAR-tests"]


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
