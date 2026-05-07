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


def test_classify_unknown_top_level_uses_folder_name_as_category():
    """An unmapped top-level folder is exposed as its own category so the
    picker mirrors whatever structure the game ships under Content/."""
    cat = classify(_p(r"WeirdFolder\Sub\Asset.psk"), GAME)
    assert cat.category == "WeirdFolder"


def test_classify_unmapped_obduction_style_categories():
    """Real-world: Obduction's Content has Avatars/Skies/MergedMeshes/etc.
    None of them are in _TOP_LEVEL_MAP — they should still show up as
    distinct categories instead of all collapsing to one bucket.
    """
    samples = [
        (r"Avatars\Foo\Foo.pskx", "Avatars"),
        (r"Skies\SkyDomeA.pskx", "Skies"),
        (r"MergedMeshes\Hunrath\Battery\SM_MERGED_BatteryA.pskx", "MergedMeshes"),
        (r"Arai\CreatureLP\Arai_Creature_LP.pskx", "Arai"),
        (r"Environments\Hunrath\Tower\X.pskx", "Environments"),
    ]
    for rel, expected in samples:
        cat = classify(_p(rel), GAME)
        assert cat.category == expected, f"{rel!r} → {cat.category!r}, expected {expected!r}"


def test_classify_keyword_override_still_wins_over_folder_name():
    """If the path mentions a known keyword, the curated category still wins
    even though the unmapped fallback would otherwise use the folder name.
    """
    # `WeaponPart` matches the `\bweapon` keyword override → "Weapons",
    # not "PaktoSDK".
    cat = classify(_p(r"PaktoSDK\WeaponPart\WP_Blaster.psk"), GAME)
    assert cat.category == "Weapons"


def test_classify_strips_game_name_folder_before_content():
    """Some unpackers preserve a game-name folder ahead of Content (e.g.
    Obduction's ``Obduction - Assets/Obduction/Content/...``). The classifier
    must slide past both the game-name folder AND the Content marker, so the
    category surfaces as the real first folder under Content — not the game
    name itself, and not "Content".
    """
    from pathlib import Path

    game_folder = r"F:\Game Directories\Obduction - Assets"
    samples = [
        (
            r"F:\Game Directories\Obduction - Assets\Obduction\Content\Avatars\Foo\Foo.pskx",
            "Avatars", "Foo",
        ),
        (
            r"F:\Game Directories\Obduction - Assets\Obduction\Content\MergedMeshes\Hunrath\Battery\SM_BatteryA.pskx",
            "MergedMeshes", "Hunrath",
        ),
        (
            r"F:\Game Directories\Obduction - Assets\Obduction\Content\Skies\SkyDomeA.pskx",
            "Skies", "General",  # only one folder under Content → no real subcategory
        ),
        (
            r"F:\Game Directories\Obduction - Assets\Obduction\Content\Foliage\SpeedTree\Tree.pskx",
            "Foliage", "SpeedTree",  # Foliage is in the curated map; subcat is real
        ),
    ]
    for psk, expected_cat, expected_sub in samples:
        cat = classify(Path(psk), game_folder)
        assert cat.category == expected_cat, (
            f"{psk!r} → category {cat.category!r}, expected {expected_cat!r}"
        )
        assert cat.subcategory == expected_sub, (
            f"{psk!r} → subcategory {cat.subcategory!r}, expected {expected_sub!r}"
        )


def test_classify_never_yields_content_or_game_as_category():
    """Direct guard: neither ``Content`` nor ``Game`` should ever show up
    as a category, regardless of how many wrapper folders sit above it.
    """
    from pathlib import Path

    game_folder = r"F:\Outer\Wrapper"
    paths_with_wrappers = [
        r"F:\Outer\Wrapper\GameName\Content\Stuff\X.pskx",
        r"F:\Outer\Wrapper\Content\Stuff\X.pskx",
        r"F:\Outer\Wrapper\Game\Stuff\X.pskx",
        r"F:\Outer\Wrapper\Some\Nested\Game\Stuff\X.pskx",
    ]
    for psk in paths_with_wrappers:
        cat = classify(Path(psk), game_folder)
        assert cat.category.lower() not in {"content", "game"}, (
            f"{psk!r} produced container as category: {cat.category!r}"
        )


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


def test_classify_case_insensitive_prefix_match():
    """Casing drift between game_folder and the on-disk path must still match.

    On Windows the same path can come back from different APIs with
    different casing. Pre-fix this silently produced Uncategorized/Unknown.
    """
    from pathlib import Path
    cat = classify(
        Path(r"C:\GameFiles\GAME\Characters\Trooper\T.psk"),
        r"C:\gamefiles\game",
    )
    assert cat.category == "Characters"
    assert cat.subcategory == "Trooper"


def test_classify_marker_fallback_finds_content_root():
    """When game_folder doesn't match, fall back to UE Content/Game markers.

    Mirrors the user's Obduction layout: the configured game_folder points
    at a sibling directory but the PSK clearly sits under a Content root.
    """
    from pathlib import Path
    cat = classify(
        Path(r"F:\Foo\Obduction\Content\MergedMeshes\Hunrath\Battery\X.pskx"),
        r"D:\WrongPath",
    )
    # We start from `Content` → strip → top-level becomes `MergedMeshes`,
    # which isn't in the curated alias map, so it surfaces as its own
    # category (per-game folder structure mirrors into the picker).
    assert cat.category == "MergedMeshes"
    assert cat.subcategory == "Hunrath"


def test_classify_uncategorized_carries_reason():
    """When all matching strategies fail, AssetCategory.reason is populated."""
    from pathlib import Path
    cat = classify(Path(r"D:\NotAGame\Assets\X.psk"), r"C:\GameFiles\Game")
    assert cat.category == "Uncategorized"
    assert cat.reason  # non-empty diagnostic
    assert "game_folder" in cat.reason or "no_parts" in cat.reason


def test_classify_reason_empty_on_success():
    """Reason field stays empty for successfully classified assets."""
    cat = classify(_p(r"Characters\B1Droid\B1Droid.psk"), GAME)
    assert cat.reason == ""


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
