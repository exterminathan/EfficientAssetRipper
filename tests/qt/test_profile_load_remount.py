"""UnpackerPanel.load_from_profile auto-remount behavior."""

from __future__ import annotations

from gui.unpacker_panel import UnpackerPanel


def _profile_with_keys(game_dir: str = "C:/games/Foo") -> dict:
    return {
        "game_dir": game_dir,
        "ue_version": "GAME_UE5_4",
        "mappings_path": "",
        "unpack_output_dir": "",
        "aes_keys": [
            {"label": "main", "guid": "0" * 32, "key": "DEADBEEF" * 8},
        ],
    }


def test_load_from_profile_does_not_auto_mount_when_never_mounted(qtbot, mocker):
    """First-ever profile open should NOT auto-mount."""
    panel = UnpackerPanel()
    qtbot.addWidget(panel)
    assert panel._mounted is False

    spy = mocker.patch.object(panel, "_mount_archives")
    panel.load_from_profile(_profile_with_keys())
    # Run any deferred QTimer.singleShot(0) callbacks the panel might have scheduled
    qtbot.wait(20)

    spy.assert_not_called()


def test_load_from_profile_auto_mounts_when_previously_mounted(qtbot, mocker):
    """If user had archives mounted, switching profiles should auto-remount."""
    panel = UnpackerPanel()
    qtbot.addWidget(panel)
    panel._mounted = True  # simulate prior mount

    spy = mocker.patch.object(panel, "_mount_archives")
    panel.load_from_profile(_profile_with_keys())
    qtbot.wait(50)

    spy.assert_called_once()


def test_load_from_profile_skips_remount_when_no_aes_keys(qtbot, mocker):
    """Even if previously mounted, an empty AES list shouldn't trigger remount."""
    panel = UnpackerPanel()
    qtbot.addWidget(panel)
    panel._mounted = True

    spy = mocker.patch.object(panel, "_mount_archives")
    profile = _profile_with_keys()
    profile["aes_keys"] = []
    panel.load_from_profile(profile)
    qtbot.wait(50)

    spy.assert_not_called()


def test_load_from_profile_skips_remount_when_no_game_dir(qtbot, mocker):
    panel = UnpackerPanel()
    qtbot.addWidget(panel)
    panel._mounted = True

    spy = mocker.patch.object(panel, "_mount_archives")
    profile = _profile_with_keys(game_dir="")
    panel.load_from_profile(profile)
    qtbot.wait(50)

    spy.assert_not_called()
