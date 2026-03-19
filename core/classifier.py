"""Classify PSK/PSKX assets into categories based on their folder path.

Works with any UE5-exported game folder. Uses the top-level and secondary
folder segments to assign a human-readable category and subcategory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# ---------------------------------------------------------------------------
# Category mapping — top-level folder → category
# Order matters: first match wins
# ---------------------------------------------------------------------------

# Exact top-level folder → category
_TOP_LEVEL_MAP: dict[str, str] = {
    "characters":       "Characters",
    "items":            "Items",
    "vehicles":         "Vehicles",
    "levels":           "Levels",
    "models":           "Models",
    "modelsr8":         "Models",
    "meshes":           "Environment",
    "effects":          "Effects",
    "worldinteracts":   "Interactables",
    "weapons":          "Weapons",
    "materials":        "Materials",
    "materialsr8":      "Materials",
    "cinematics":       "Cinematics",
    "audio":            "Audio",
    "ui":               "UI",
    "lighting":         "Lighting",
    "foliage":          "Foliage",
    "techart":          "Tech Art",
    "houdiniengine":    "Procedural",
    "narrative":        "Narrative",
    "globaldata":       "Global",
    "developers":       "Developer",
}

# Keyword patterns that override when found anywhere in the relative path
_KEYWORD_OVERRIDES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bweapon", re.IGNORECASE),        "Weapons"),
    (re.compile(r"\bvehicle", re.IGNORECASE),       "Vehicles"),
    (re.compile(r"\bfoliage", re.IGNORECASE),       "Foliage"),
    (re.compile(r"\bcharacter", re.IGNORECASE),     "Characters"),
]


@dataclass
class AssetCategory:
    """Category + subcategory for an asset."""
    category: str       # e.g. "Characters"
    subcategory: str    # e.g. "B1Droid"  (second-level folder or "General")

    @property
    def display(self) -> str:
        return f"{self.category} / {self.subcategory}"


def classify(psk_path: Path, game_folder: str) -> AssetCategory:
    """Classify an asset based on its path relative to the game folder.

    Args:
        psk_path: Absolute path to the PSK/PSKX file.
        game_folder: The root game content folder path.

    Returns:
        AssetCategory with category and subcategory strings.
    """
    try:
        rel = psk_path.relative_to(game_folder)
    except ValueError:
        return AssetCategory("Uncategorized", "Unknown")

    parts = rel.parts  # e.g. ("Game", "Characters", "B1Droid", "B1Droid.psk")

    if not parts:
        return AssetCategory("Uncategorized", "Unknown")

    # Skip common container folders that aren't real categories
    _SKIP = {"game", "content"}
    while len(parts) > 1 and parts[0].lower() in _SKIP:
        parts = parts[1:]

    top = parts[0].lower()
    category = _TOP_LEVEL_MAP.get(top)

    # If no direct map, check keyword overrides on the full relative path
    if category is None:
        rel_str = str(rel)
        for pattern, cat in _KEYWORD_OVERRIDES:
            if pattern.search(rel_str):
                category = cat
                break

    if category is None:
        category = "Other"

    # Subcategory = second-level folder if available, otherwise "General"
    subcategory = parts[1] if len(parts) > 2 else "General"

    return AssetCategory(category=category, subcategory=subcategory)


def get_all_categories() -> list[str]:
    """Return all known category names in display order."""
    seen = set()
    ordered = []
    for cat in _TOP_LEVEL_MAP.values():
        if cat not in seen:
            seen.add(cat)
            ordered.append(cat)
    ordered.extend(["Other", "Uncategorized"])
    return ordered
