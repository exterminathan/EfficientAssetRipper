"""Integration tests for `core.profile_manager.ProfileManager` (real disk I/O)."""

from __future__ import annotations

import json

import pytest

from core.profile_manager import ProfileManager

pytestmark = pytest.mark.integration


def test_list_profiles_empty_dir(tmp_profiles_dir):
    pm = ProfileManager()
    assert pm.list_profiles() == []


def test_create_then_load_profile_roundtrip(tmp_profiles_dir):
    pm = ProfileManager()
    pm.create_profile("Game1", {"game_dir": r"C:\Games\Game1"})
    loaded = pm.load_profile("Game1")
    assert loaded["name"] == "Game1"
    assert loaded["game_dir"] == r"C:\Games\Game1"


def test_save_profile_writes_valid_json(tmp_profiles_dir):
    pm = ProfileManager()
    pm.create_profile("DiskCheck", {"game_dir": r"X:\X"})
    file = tmp_profiles_dir / "DiskCheck.json"
    assert file.is_file()
    content = json.loads(file.read_text(encoding="utf-8"))
    assert content["name"] == "DiskCheck"


def test_rename_profile_moves_file(tmp_profiles_dir):
    pm = ProfileManager()
    pm.create_profile("Old", {"game_dir": "x"})
    pm.rename_profile("Old", "New")
    assert not (tmp_profiles_dir / "Old.json").exists()
    assert (tmp_profiles_dir / "New.json").is_file()
    loaded = pm.load_profile("New")
    assert loaded["name"] == "New"


def test_rename_profile_to_existing_raises(tmp_profiles_dir):
    pm = ProfileManager()
    pm.create_profile("A", {})
    pm.create_profile("B", {})
    with pytest.raises(FileExistsError):
        pm.rename_profile("A", "B")


def test_delete_profile_removes_file(tmp_profiles_dir):
    pm = ProfileManager()
    pm.create_profile("ToDelete", {})
    pm.delete_profile("ToDelete")
    assert not (tmp_profiles_dir / "ToDelete.json").exists()


def test_load_missing_profile_raises(tmp_profiles_dir):
    pm = ProfileManager()
    with pytest.raises(FileNotFoundError):
        pm.load_profile("DoesNotExist")


@pytest.mark.parametrize(
    "name,expected_ok",
    [
        ("ValidName", True),
        ("Valid Name 123", True),
        ("", False),
        ("   ", False),
        ("with/slash", False),
        ("with\\back", False),
        ("with:colon", False),
        ("with*star", False),
        ('with"quote', False),
        ("with<lt", False),
        (".", False),
        ("..", False),
        ("X" * 101, False),
    ],
)
def test_is_valid_name(name, expected_ok):
    ok, msg = ProfileManager.is_valid_name(name)
    assert ok is expected_ok, f"name={name!r} unexpected (msg={msg!r})"


def test_migrate_from_qsettings_creates_default_profile(tmp_profiles_dir, mock_qsettings):
    """When no profiles exist, migration seeds a 'Default' profile from QSettings."""
    import config

    config.set("game_folder", r"C:\Games\Migrated")
    config.set("unpack_ue_version", "GAME_UE5_3")

    pm = ProfileManager()
    name = pm.migrate_from_qsettings(config)
    assert name == "Default"
    assert "Default" in pm.list_profiles()
    profile = pm.load_profile("Default")
    assert profile["game_dir"] == r"C:\Games\Migrated"
    assert profile["ue_version"] == "GAME_UE5_3"


def test_migrate_from_qsettings_skips_when_profiles_already_exist(tmp_profiles_dir, mock_qsettings):
    pm = ProfileManager()
    pm.create_profile("Existing", {})
    import config

    result = pm.migrate_from_qsettings(config)
    assert result is None


def test_profile_exists(tmp_profiles_dir):
    pm = ProfileManager()
    assert pm.profile_exists("X") is False
    pm.create_profile("X", {})
    assert pm.profile_exists("X") is True
