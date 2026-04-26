"""Smoke tests for `gui.main_window.MainWindow` construction + menu shape."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.qt, pytest.mark.gui]


@pytest.fixture
def main_window(qtbot, mock_qsettings, tmp_profiles_dir):
    """Construct MainWindow with isolated settings + profiles. Not shown."""
    # Mark first-run setup as complete so the wizard doesn't fire during tests.
    import config
    config.set("setup_complete", "1")
    from gui.main_window import MainWindow
    win = MainWindow()
    qtbot.addWidget(win)
    return win


def test_main_window_constructs_with_default_config(main_window):
    # Title may include the active profile name appended (e.g. "EfficientAssetRipper — Default")
    assert main_window.windowTitle().startswith("EfficientAssetRipper")


def test_main_window_creates_profile_bar(main_window):
    assert main_window._profile_bar is not None


def test_main_window_creates_queue_panel(main_window):
    assert main_window._queue is not None
    assert main_window._queue._table.rowCount() == 0


def test_main_window_creates_log_viewer(main_window):
    assert main_window._log is not None


def test_menu_actions_present(main_window):
    menu_titles = [
        a.menu().title() for a in main_window.menuBar().actions() if a.menu()
    ]
    assert "File" in menu_titles
    assert "Tools" in menu_titles
    assert "Window" in menu_titles


def test_status_bar_initial_message(main_window):
    msg = main_window._statusbar.currentMessage()
    assert "Ready" in msg or "Configure" in msg or "configure" in msg


def test_left_tabs_have_browser_picker_unpacker(main_window):
    titles = [main_window._left_tabs.tabText(i) for i in range(main_window._left_tabs.count())]
    assert "Asset Browser" in titles
    assert "PSK Picker" in titles
    assert "Unpacker" in titles


def test_right_tabs_have_queue_log_and_combiner(main_window):
    titles = [main_window._right_tabs.tabText(i) for i in range(main_window._right_tabs.count())]
    assert "Queue / Log" in titles
    assert "Blend Combiner" in titles


def test_is_busy_false_initially(main_window):
    assert main_window._is_busy() is False


def test_default_profile_seeded_on_first_launch(main_window, tmp_profiles_dir):
    """First launch with no profiles should auto-create a Default profile."""
    profiles = list(tmp_profiles_dir.glob("*.json"))
    assert len(profiles) >= 1
    assert any(p.stem == "Default" for p in profiles)
