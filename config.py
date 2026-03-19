"""Persistent application settings via QSettings."""

import json
from pathlib import Path
from PySide6.QtCore import QSettings
from _base import base_dir

_DEFAULTS = {
    "game_folder": "",
    "blender_exe": "",
    "output_dir": "",
    "everything_dll": "",
    "psk_addon_name": "bl_ext.blender_org.io_scene_psk_psa",
    "timeout_seconds": 120,
    "presets_path": str(base_dir() / "data" / "texture_presets.json"),
    "cue4parse_cli": "",
    "unpack_output_dir": "",
    "aes_keys": "[]",
    "unpack_ue_version": "GAME_UE5_4",
    "export_texture_format": "png",
    "export_audio_format": "wav",
    "active_profile": "Default",
    "color_scheme": "Dusk",
    "custom_schemes": "{}",
}

_qs = QSettings("EfficientAssetRipper", "EfficientAssetRipper")

# Migrate stale addon name from old default
_OLD_ADDON = "io_import_scene_unreal_psa_psk_280"
if _qs.value("psk_addon_name", "") == _OLD_ADDON:
    _qs.setValue("psk_addon_name", _DEFAULTS["psk_addon_name"])


def get(key: str) -> str:
    return str(_qs.value(key, _DEFAULTS.get(key, "")))


def get_int(key: str) -> int:
    return int(_qs.value(key, _DEFAULTS.get(key, 0)))


def set(key: str, value) -> None:  # noqa: A001
    _qs.setValue(key, value)


def get_presets_path() -> Path:
    p = get("presets_path")
    return Path(p) if p else base_dir() / "data" / "texture_presets.json"


def load_presets() -> dict:
    path = get_presets_path()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def all_keys() -> list[str]:
    return list(_DEFAULTS.keys())
