"""Unit tests for `core.classifier` — pure path-based asset classification."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from core.classifier import AssetCategory, classify, get_all_categories

pytestmark = pytest.mark.unit


GAME = r"C:\GameFiles\Game"


def _p(rel: str):
    """Build a fake absolute path under GAME for classification."""
    from pathlib import Path
    return Path(GAME) / rel


def test_classify_characters_path():
    cat = classify(_p(r"Characters\B1Droid\B1Droid.psk"), GAME)
    assert cat.category == "Characters"
    assert cat.subcategory == "B1Droid"


def test_classify_skips_game_and_content_containers():
    """Both `game` and `content` containers should be stripped before mapping."""
    cat1 = classify(_p(r"Game\Characters\B1\B1.psk"), GAME)
    cat2 = classify(_p(r"Content\Characters\B1\B1.psk"), GAME)
    assert cat1.category == "Characters"
    assert cat2.category == "Characters"


def test_classify_keyword_override_weapon_in_subpath():
    """Even with no top-level mapping, the `weapon` keyword should win."""
    cat = classify(_p(r"PaktoSDK\WeaponPart\WP_Blaster.psk"), GAME)
    assert cat.category == "Weapons"


def test_classify_modelsr8_maps_to_models():
    """Jedi Survivor uses 'modelsr8'; ensure it lands in 'Models'."""
    cat = classify(_p(r"modelsr8\Trooper\SK_Trooper.psk"), GAME)
    assert cat.category == "Models"


def test_classify_unknown_top_level_falls_back_to_other():
    cat = classify(_p(r"WeirdFolder\Sub\Asset.psk"), GAME)
    assert cat.category == "Other"


def test_classify_path_outside_game_folder_returns_uncategorized():
    from pathlib import Path
    cat = classify(Path(r"D:\NotTheGame\Assets\X.psk"), GAME)
    assert cat.category == "Uncategorized"
    assert cat.subcategory == "Unknown"


def test_classify_subcategory_general_when_too_short():
    """When there's no second-level folder, subcategory is 'General'."""
    cat = classify(_p(r"Items\X.psk"), GAME)
    assert cat.category == "Items"
    assert cat.subcategory == "General"


def test_classify_subcategory_uses_second_level_folder():
    cat = classify(_p(r"Vehicles\XWing\Variants\XWing01.psk"), GAME)
    assert cat.category == "Vehicles"
    assert cat.subcategory == "XWing"


def test_get_all_categories_no_dupes_includes_other_uncategorized():
    cats = get_all_categories()
    assert len(cats) == len(set(cats)), f"duplicate categories: {cats}"
    assert "Other" in cats
    assert "Uncategorized" in cats


def test_asset_category_display_format():
    cat = AssetCategory(category="Characters", subcategory="Trooper")
    assert cat.display == "Characters / Trooper"


def test_classify_real_scan_paths_smoke(jedi_scan_dict):
    """Every PSK path in the real Jedi scan should classify without raising."""
    from pathlib import Path
    raised = []
    for asset in jedi_scan_dict.get("assets", []):
        psk = Path(asset["psk_path"])
        try:
            classify(psk, jedi_scan_dict.get("game_folder", ""))
        except Exception as e:
            raised.append((str(psk), repr(e)))
    assert not raised, f"classify() raised on {len(raised)} paths: {raised[:3]}"
