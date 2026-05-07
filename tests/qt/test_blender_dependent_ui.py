"""MainWindow gates Blender-touching UI on ``is_blender_available()``."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.qt, pytest.mark.gui]


@pytest.fixture
def main_window_no_blender(qtbot, mock_qsettings, tmp_profiles_dir, mocker):
    """MainWindow built with ``is_blender_available()`` mocked to False.

    The patch must land BEFORE construction since ``_refresh_blender_dependent_ui``
    runs during __init__.
    """
    import config
    config.set("setup_complete", "1")
    mocker.patch("core.blender_runner.is_blender_available", return_value=False)

    from gui.main_window import MainWindow
    win = MainWindow()
    qtbot.addWidget(win)
    return win


@pytest.fixture
def main_window_with_blender(qtbot, mock_qsettings, tmp_profiles_dir, mocker):
    import config
    config.set("setup_complete", "1")
    mocker.patch("core.blender_runner.is_blender_available", return_value=True)

    from gui.main_window import MainWindow
    win = MainWindow()
    qtbot.addWidget(win)
    return win


def test_blend_combiner_dock_disabled_without_blender(main_window_no_blender):
    win = main_window_no_blender
    assert win._combiner_dock is not None
    assert win._combiner_dock.isEnabled() is False


def test_process_queue_button_disabled_without_blender(main_window_no_blender):
    win = main_window_no_blender
    assert win._queue._process_btn.isEnabled() is False
    assert "Blender" in win._queue._process_btn.toolTip()


def test_status_bar_warns_without_blender(main_window_no_blender):
    win = main_window_no_blender
    msg = win._statusbar.currentMessage()
    assert "Blender" in msg


def test_tools_blend_combiner_action_disabled_without_blender(main_window_no_blender):
    win = main_window_no_blender
    assert win._blend_combiner_action is not None
    assert win._blend_combiner_action.isEnabled() is False


def test_blend_combiner_dock_enabled_with_blender(main_window_with_blender):
    win = main_window_with_blender
    assert win._combiner_dock.isEnabled() is True
    assert win._combiner_dock.windowTitle() == "Blend Combiner"


def test_process_queue_button_enabled_with_blender(main_window_with_blender):
    win = main_window_with_blender
    assert win._queue._process_btn.isEnabled() is True
