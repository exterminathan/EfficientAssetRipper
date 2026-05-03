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


# ---------------------------------------------------------------------------
# Path-traversal hardening
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "evil_name",
    [
        "..\\evil",
        "../evil",
        "../../etc/passwd",
        r"C:\Windows\System32\foo",  # absolute path masquerading as a name
        "name/with/slash",
        "name\\with\\backslash",
        "name\x00null",              # NUL byte
        "CON",                       # reserved Windows device names
        "PRN",
        "NUL",
        "AUX",
        "COM1",
        "lpt9",                      # case-insensitive
        ".",
        "..",
        "",
        "   ",
    ],
)
def test_load_profile_refuses_traversal_or_reserved_name(tmp_profiles_dir, evil_name):
    pm = ProfileManager()
    with pytest.raises((ValueError, FileNotFoundError)):
        pm.load_profile(evil_name)


@pytest.mark.parametrize(
    "evil_name",
    ["..\\evil", "../evil", "CON", "PRN", "name/with/slash"],
)
def test_save_profile_refuses_traversal_or_reserved_name(tmp_profiles_dir, evil_name):
    pm = ProfileManager()
    with pytest.raises(ValueError):
        pm.save_profile(evil_name, {})


@pytest.mark.parametrize(
    "evil_name",
    ["..\\evil", "CON", "name/with/slash"],
)
def test_rename_profile_refuses_traversal_targets(tmp_profiles_dir, evil_name):
    pm = ProfileManager()
    pm.create_profile("Source", {})
    with pytest.raises(ValueError):
        pm.rename_profile("Source", evil_name)
    # Source must still exist — rejection happens before the swap.
    assert pm.profile_exists("Source")


def test_delete_profile_refuses_traversal(tmp_profiles_dir):
    pm = ProfileManager()
    with pytest.raises(ValueError):
        pm.delete_profile("..\\evil")


def test_profile_exists_returns_false_for_invalid_names(tmp_profiles_dir):
    pm = ProfileManager()
    assert pm.profile_exists("..\\evil") is False
    assert pm.profile_exists("CON") is False
    assert pm.profile_exists("name/with/slash") is False


def test_list_profiles_filters_invalid_stems(tmp_profiles_dir):
    """Files matching reserved/invalid names on disk must not be listed."""
    # The filesystem lets us write CON.json (since we bypass _safe_path), but
    # list_profiles should silently skip it.
    pm = ProfileManager()
    pm.create_profile("Good", {})
    # Manually write a bogus reserved-name profile.
    (tmp_profiles_dir / "CON.json").write_text("{}", encoding="utf-8")
    listed = pm.list_profiles()
    assert "Good" in listed
    assert "CON" not in listed


def test_load_profile_resets_aes_keys_when_corrupt(tmp_profiles_dir):
    """A profile JSON shouldn't crash the loader if aes_keys is the wrong type."""
    pm = ProfileManager()
    pm.create_profile("Bad", {})
    # Hand-craft a profile with aes_keys as a string instead of a list.
    bad = json.loads((tmp_profiles_dir / "Bad.json").read_text(encoding="utf-8"))
    bad["aes_keys"] = "not-a-list"
    (tmp_profiles_dir / "Bad.json").write_text(json.dumps(bad), encoding="utf-8")
    loaded = pm.load_profile("Bad")
    assert loaded["aes_keys"] == []


def test_auto_save_paths_round_trip(tmp_profiles_dir):
    """The new auto_save_paths flag must persist as a real bool."""
    pm = ProfileManager()
    pm.create_profile("OptIn", {"auto_save_paths": True})
    loaded = pm.load_profile("OptIn")
    assert loaded["auto_save_paths"] is True

    pm.save_profile("OptIn", {**loaded, "auto_save_paths": False})
    loaded_back = pm.load_profile("OptIn")
    assert loaded_back["auto_save_paths"] is False


def test_auto_save_paths_defaults_false_when_absent(tmp_profiles_dir):
    """Existing profiles missing the field should load with the default."""
    pm = ProfileManager()
    pm.create_profile("Legacy", {})
    legacy = json.loads((tmp_profiles_dir / "Legacy.json").read_text(encoding="utf-8"))
    legacy.pop("auto_save_paths", None)
    (tmp_profiles_dir / "Legacy.json").write_text(json.dumps(legacy), encoding="utf-8")

    loaded = pm.load_profile("Legacy")
    assert loaded["auto_save_paths"] is False


def test_auto_save_paths_coerces_truthy_int_to_bool(tmp_profiles_dir):
    """If a profile JSON has auto_save_paths=1 (legacy/manual edit), coerce to True."""
    pm = ProfileManager()
    pm.create_profile("Coerce", {})
    raw = json.loads((tmp_profiles_dir / "Coerce.json").read_text(encoding="utf-8"))
    raw["auto_save_paths"] = 1
    (tmp_profiles_dir / "Coerce.json").write_text(json.dumps(raw), encoding="utf-8")

    loaded = pm.load_profile("Coerce")
    assert loaded["auto_save_paths"] is True
