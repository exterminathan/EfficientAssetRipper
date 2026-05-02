"""Game profile management — CRUD operations on per-game JSON profiles."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from _base import base_dir

log = logging.getLogger(__name__)

_PROFILES_DIR = base_dir() / "profiles"

# Characters not allowed in profile (file) names on Windows
_INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')

# Reserved Windows device names (case-insensitive, with or without extension)
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Keys that live per-profile (everything else stays in global QSettings)
PROFILE_KEYS = {
    "game_dir",
    "ue_version",
    "aes_keys",
    "unpack_output_dir",
    "blender_output_dir",
    "mappings_path",
    "scan_cache_file",
    "psk_processed",
    "color_scheme",
    "custom_schemes",
}

_EMPTY_PROFILE: dict = {
    "name": "",
    "game_dir": "",
    "ue_version": "GAME_UE5_4",
    "aes_keys": [],
    "unpack_output_dir": "",
    "blender_output_dir": "",
    "mappings_path": "",
    "scan_cache_file": "",
    "psk_processed": [],
    "color_scheme": "",
    "custom_schemes": {},
}


def _safe_path(name: str) -> Path:
    """Resolve ``profiles/<name>.json`` if and only if *name* is benign.

    Raises ``ValueError`` for empty/oversized names, names containing path
    separators or shell metacharacters, reserved Windows device names, and
    any name whose resolved file would escape ``_PROFILES_DIR`` (e.g.
    ``..\\evil`` or an absolute path).
    """
    if not isinstance(name, str):
        raise ValueError(f"Profile name must be a string, got {type(name).__name__}")

    ok, reason = ProfileManager.is_valid_name(name)
    if not ok:
        raise ValueError(f"Invalid profile name: {reason}")

    stripped = name.strip()
    if stripped.upper() in _RESERVED_NAMES:
        raise ValueError(f"Invalid profile name: '{stripped}' is a reserved Windows device name")

    candidate = (_PROFILES_DIR / f"{stripped}.json").resolve()
    parent = _PROFILES_DIR.resolve()
    try:
        candidate.relative_to(parent)
    except ValueError as e:
        raise ValueError(
            f"Profile name resolves outside the profiles directory: {stripped!r}"
        ) from e
    if candidate.parent != parent:
        raise ValueError(
            f"Profile name resolves outside the profiles directory: {stripped!r}"
        )
    return candidate


class ProfileManager:
    """Manages per-game profile JSON files in the ``profiles/`` directory."""

    def __init__(self) -> None:
        _PROFILES_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def profiles_dir(self) -> Path:
        return _PROFILES_DIR

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list_profiles(self) -> list[str]:
        """Return sorted list of profile names (stems of JSON files).

        Stems failing :meth:`is_valid_name` are filtered out — a malformed
        on-disk file should not crash the picker.
        """
        names: list[str] = []
        for p in _PROFILES_DIR.glob("*.json"):
            stem = p.stem
            ok, _ = self.is_valid_name(stem)
            if not ok:
                continue
            if stem.upper() in _RESERVED_NAMES:
                continue
            names.append(stem)
        return sorted(names)

    def load_profile(self, name: str) -> dict:
        """Load a profile by name.  Returns a dict (empty-profile defaults for missing keys)."""
        path = _safe_path(name)
        if not path.is_file():
            raise FileNotFoundError(f"Profile not found: {name}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Ensure all expected keys exist — and that types match the defaults.
        for k, v in _EMPTY_PROFILE.items():
            data.setdefault(k, v)
            # Reject obviously corrupt/malicious values for collection keys.
            if k == "aes_keys" and not isinstance(data[k], list):
                log.warning("profile %s: aes_keys is %s, resetting to []", name, type(data[k]).__name__)
                data[k] = []
            elif k == "psk_processed" and not isinstance(data[k], list):
                data[k] = []
            elif k == "custom_schemes" and not isinstance(data[k], dict):
                data[k] = {}
        data["name"] = name.strip()
        return data

    def save_profile(self, name: str, data: dict) -> None:
        """Write *data* to ``profiles/<name>.json``."""
        path = _safe_path(name)
        data["name"] = name.strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log.info("Saved profile: %s", path)

    def delete_profile(self, name: str) -> None:
        path = _safe_path(name)
        if path.is_file():
            path.unlink()
            log.info("Deleted profile: %s", name)

    def rename_profile(self, old: str, new: str) -> None:
        """Rename a profile on disk.  Updates the ``name`` field inside."""
        old_path = _safe_path(old)
        new_path = _safe_path(new)
        if not old_path.is_file():
            raise FileNotFoundError(f"Profile not found: {old}")
        if new_path.exists():
            raise FileExistsError(f"Profile already exists: {new}")
        data = self.load_profile(old)
        data["name"] = new.strip()
        with open(new_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        old_path.unlink()
        log.info("Renamed profile: %s → %s", old, new)

    def profile_exists(self, name: str) -> bool:
        try:
            return _safe_path(name).is_file()
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # New profile helper
    # ------------------------------------------------------------------

    def create_profile(self, name: str, data: dict | None = None) -> dict:
        """Create a new profile with defaults, optionally merging *data*."""
        profile = dict(_EMPTY_PROFILE)
        if data:
            profile.update(data)
        profile["name"] = name.strip()
        self.save_profile(name, profile)
        return profile

    # ------------------------------------------------------------------
    # Migration from legacy QSettings
    # ------------------------------------------------------------------

    def migrate_from_qsettings(self, cfg_module) -> str | None:
        """If no profiles exist, create a 'Default' profile from existing QSettings.

        Returns the created profile name, or None if migration was skipped.
        """
        if self.list_profiles():
            return None  # profiles already exist

        import hashlib

        game_dir = cfg_module.get("game_folder") or ""
        ue_version = cfg_module.get("unpack_ue_version") or "GAME_UE5_4"
        unpack_out = cfg_module.get("unpack_output_dir") or ""
        blender_out = cfg_module.get("output_dir") or ""
        mappings = ""

        aes_keys: list[dict] = []
        raw_keys = cfg_module.get("aes_keys") or "[]"
        try:
            aes_keys = json.loads(raw_keys)
        except json.JSONDecodeError:
            pass

        # Determine scan cache filename for this game_dir
        scan_cache_file = ""
        if game_dir:
            folder_hash = hashlib.md5(game_dir.encode()).hexdigest()[:12]
            scan_cache_file = f"scan_{folder_hash}.json"

        profile = self.create_profile("Default", {
            "game_dir": game_dir,
            "ue_version": ue_version,
            "aes_keys": aes_keys,
            "unpack_output_dir": unpack_out,
            "blender_output_dir": blender_out,
            "mappings_path": mappings,
            "scan_cache_file": scan_cache_file,
            "psk_processed": [],
        })
        log.info("Migrated QSettings → Default profile (game_dir=%s)", game_dir)
        return "Default"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def is_valid_name(name: str) -> tuple[bool, str]:
        """Check if *name* is a valid profile name.  Returns (ok, reason)."""
        if not isinstance(name, str):
            return False, "Name must be a string."
        name = name.strip()
        if not name:
            return False, "Name cannot be empty."
        if _INVALID_CHARS.search(name):
            return False, 'Name cannot contain: \\ / : * ? " < > |'
        if name in (".", ".."):
            return False, "Invalid name."
        if len(name) > 100:
            return False, "Name too long (max 100 characters)."
        # Block control characters (NUL, newlines, tab, …) which would let
        # the resolved path slip through filesystem APIs in unexpected ways.
        if any(ord(c) < 0x20 for c in name):
            return False, "Name cannot contain control characters."
        if name.upper() in _RESERVED_NAMES:
            return False, f"'{name}' is a reserved Windows device name."
        return True, ""
