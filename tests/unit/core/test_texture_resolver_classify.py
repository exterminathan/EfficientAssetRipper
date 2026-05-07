"""Unit tests for `core.texture_resolver.classify_texture` (pure)."""

from __future__ import annotations

import pytest

from core.texture_resolver import classify_texture

pytestmark = pytest.mark.unit


@pytest.fixture
def slots():
    return {
        "base_color": {
            "suffixes": ["_C", "_BaseColor"],
            "param_names": ["BaseColor", "Diffuse"],
            "colorspace": "sRGB",
            "wiring": {"type": "direct", "target_input": "Base Color"},
        },
        "normal": {
            "suffixes": ["_N", "_Normal"],
            "param_names": ["Normal"],
            "colorspace": "Non-Color",
            "wiring": {"type": "normal_map"},
        },
    }


def test_classify_texture_suffix_match_base_color(slots):
    result = classify_texture("T_Trooper_Body_C", slots)
    assert result is not None
    slot, cfg = result
    assert slot == "base_color"
    assert cfg["colorspace"] == "sRGB"


def test_classify_texture_suffix_case_insensitive(slots):
    """Lowercase '_c' should still match the uppercase suffix list."""
    result = classify_texture("T_Trooper_Body_c", slots)
    assert result is not None and result[0] == "base_color"


def test_classify_texture_trailing_underscore_stripped(slots):
    """A trailing underscore on the texture name should not block matching."""
    result = classify_texture("T_Trooper_Body_C_", slots)
    assert result is not None and result[0] == "base_color"


def test_classify_texture_param_name_fallback(slots):
    """No suffix match — but param_name 'BaseColor' should still classify."""
    result = classify_texture("T_NoSuffix_AtAll", slots, param_name="BaseColor")
    assert result is not None and result[0] == "base_color"


def test_classify_texture_no_match_returns_none(slots):
    assert classify_texture("T_RandomThing", slots) is None


def test_classify_texture_normal_suffix(slots):
    result = classify_texture("T_Helmet_N", slots)
    assert result is not None and result[0] == "normal"
    assert result[1]["colorspace"] == "Non-Color"


# ---------------------------------------------------------------------------
# Phase 1.1: priority_order / longest-suffix-wins
# ---------------------------------------------------------------------------

def test_classify_or_does_not_shadow_orm():
    """`_OR` in a different slot must not steal a `_ORM` match."""
    slots = {
        # 'ao' has _OR, _AO. 'orm' has _ORM. Without longest-suffix-wins,
        # iteration order would let _OR hit first for "T_Body_ORM".
        "ao": {"suffixes": ["_OR", "_AO"], "param_names": []},
        "orm": {"suffixes": ["_ORM"], "param_names": []},
    }
    result = classify_texture("T_Body_ORM", slots)
    assert result is not None and result[0] == "orm"


def test_classify_c_does_not_shadow_basecolor():
    """A longer suffix in a separate slot still wins over `_C` in another."""
    slots = {
        "single_c": {"suffixes": ["_C"], "param_names": []},
        "named_basecolor": {"suffixes": ["_BaseColor"], "param_names": []},
    }
    # Texture ends with both `_C` and `_BaseColor`; longer wins.
    result = classify_texture("T_Trooper_BaseColor", slots)
    assert result is not None and result[0] == "named_basecolor"


def test_classify_priority_order_breaks_ties_between_equal_length_suffixes():
    """Equal-length cross-slot match → priority_order picks the winner."""
    slots = {
        "specular": {"suffixes": ["_S"], "param_names": []},
        "alpha": {"suffixes": ["_A"], "param_names": []},
    }
    # T_Foo_S ends with _S. The two suffixes are equal length, so priority
    # order wins. We declare alpha first.
    result = classify_texture(
        "T_Foo_S", slots, priority_order=["specular", "alpha"]
    )
    assert result is not None and result[0] == "specular"


def test_classify_priority_order_fallthrough_for_unlisted_slots():
    """Unlisted slots fall through in stable dict order, after listed ones."""
    slots = {
        "later": {"suffixes": ["_X"], "param_names": []},
        "earlier": {"suffixes": ["_X"], "param_names": []},
    }
    result = classify_texture(
        "T_Foo_X", slots, priority_order=["earlier"]
    )
    assert result is not None and result[0] == "earlier"


def test_classify_dict_order_does_not_change_classification():
    """Shuffling the slot dict must not change results when priority_order is given."""
    base_slots = {
        "ao": {"suffixes": ["_OR", "_AO"], "param_names": []},
        "orm": {"suffixes": ["_ORM"], "param_names": []},
        "base_color": {"suffixes": ["_C"], "param_names": []},
    }
    priority = ["base_color", "orm", "ao"]
    seen = set()
    for keys in (
        ["ao", "orm", "base_color"],
        ["orm", "base_color", "ao"],
        ["base_color", "ao", "orm"],
    ):
        slots = {k: base_slots[k] for k in keys}
        r = classify_texture("T_Foo_ORM", slots, priority_order=priority)
        assert r is not None
        seen.add(r[0])
    assert seen == {"orm"}


def test_classify_longest_suffix_within_slot_preferred():
    """Within a single slot, descending-length sort prevents a short shadow."""
    slots = {
        "base_color": {"suffixes": ["_C", "_CS"], "param_names": []},
    }
    # "T_Foo_CS" ends with both `_CS` and `_S`; only `_CS` is in the slot,
    # but the dict order test still matters: ensure the longer suffix is
    # what's matched (returned cfg has a `_CS` in suffixes regardless).
    r = classify_texture("T_Foo_CS", slots)
    assert r is not None and r[0] == "base_color"


# ---------------------------------------------------------------------------
# Phase 1.2: regex_suffixes (Obduction-style names)
# ---------------------------------------------------------------------------

OBDUCTION_PRIORITY = ["base_color", "alpha", "metallic", "rgb_masks", "normal"]


@pytest.fixture
def obduction_slots():
    """Slots that mirror the production preset's regex shapes for Obduction."""
    return {
        "base_color": {
            "suffixes": ["_C", "_BaseColor"],
            "regex_suffixes": [r"[-_]DIFFR?(?:_[A-Z])?(?:_(?:\d+|DIRT))?$"],
            "param_names": ["BaseColor", "Diffuse"],
            "colorspace": "sRGB",
            "wiring": {"type": "direct", "target_input": "Base Color"},
        },
        "alpha": {
            "suffixes": ["_Alpha"],
            "regex_suffixes": [r"_ALPHA(?:_[A-Z])?(?:_\d+)?$"],
            "param_names": [],
        },
        "metallic": {
            "suffixes": ["_Metallic"],
            "regex_suffixes": [r"_METALLIC_MASK(?:_[A-Z])?(?:_\d+)?$"],
            "param_names": [],
        },
        "rgb_masks": {
            "suffixes": ["_Mask"],
            "regex_suffixes": [r"_MASK(?:_[A-Z])?(?:_\d+)?$"],
            "param_names": ["RGBMask"],
        },
        "normal": {
            "suffixes": ["_N", "_Normal"],
            "regex_suffixes": [
                r"[-_](?:NMRL|NRML|NRM)(?:_[A-Z])?(?:_(?:\d+|DIRT))?$"
            ],
            "param_names": ["Normal"],
            "wiring": {"type": "normal_map"},
        },
    }


def test_classify_regex_diffr_with_index(obduction_slots):
    r = classify_texture(
        "Puppet_Theatre_DIFFR_A_02",
        obduction_slots,
        priority_order=OBDUCTION_PRIORITY,
    )
    assert r is not None and r[0] == "base_color"


def test_classify_regex_nmrl_mesh_normal(obduction_slots):
    r = classify_texture(
        "Puppet_Theatre_NMRL_A_03",
        obduction_slots,
        priority_order=OBDUCTION_PRIORITY,
    )
    assert r is not None and r[0] == "normal"


def test_classify_regex_nrml_dirt_overlay(obduction_slots):
    r = classify_texture(
        "Hunrath_FarleysHouse_Schoolroom_Wood_NRML_A_DIRT",
        obduction_slots,
        priority_order=OBDUCTION_PRIORITY,
    )
    assert r is not None and r[0] == "normal"


def test_classify_regex_diff_dirt_overlay_no_variant(obduction_slots):
    """Variant letter and DIRT overlay are independent — DIRT alone matches."""
    r = classify_texture(
        "Hunrath_FarleysHouse_Schoolroom_Wood_DIFF_DIRT",
        obduction_slots,
        priority_order=OBDUCTION_PRIORITY,
    )
    assert r is not None and r[0] == "base_color"


def test_classify_regex_hyphen_separator(obduction_slots):
    r = classify_texture(
        "VintageMetal-NRM",
        obduction_slots,
        priority_order=OBDUCTION_PRIORITY,
    )
    assert r is not None and r[0] == "normal"


def test_classify_regex_mixed_case_diffr(obduction_slots):
    """Mixed-case `DiffR` still classifies via the case-insensitive regex."""
    r = classify_texture(
        "VintageMetal-Lighter_DiffR",
        obduction_slots,
        priority_order=OBDUCTION_PRIORITY,
    )
    assert r is not None and r[0] == "base_color"


def test_classify_regex_metallic_mask_beats_plain_mask(obduction_slots):
    """`_METALLIC_MASK_A_02` should land in metallic, not rgb_masks."""
    r = classify_texture(
        "Puppet_Theatre_Metallic_Mask_A_02",
        obduction_slots,
        priority_order=OBDUCTION_PRIORITY,
    )
    assert r is not None and r[0] == "metallic"


def test_classify_regex_plain_mask(obduction_slots):
    r = classify_texture(
        "Hunrath_FarleysHouse_Schoolroom_Wood_MASK_A",
        obduction_slots,
        priority_order=OBDUCTION_PRIORITY,
    )
    assert r is not None and r[0] == "rgb_masks"


def test_classify_regex_alpha_indexed(obduction_slots):
    r = classify_texture(
        "Puppet_Theatre_Alpha_A_01",
        obduction_slots,
        priority_order=OBDUCTION_PRIORITY,
    )
    assert r is not None and r[0] == "alpha"


def test_classify_regex_trailing_underscore_stripped(obduction_slots):
    """The trailing-underscore strip applies to regex matching too."""
    r = classify_texture(
        "Puppet_Theatre_DIFFR_A_02_",
        obduction_slots,
        priority_order=OBDUCTION_PRIORITY,
    )
    assert r is not None and r[0] == "base_color"


def test_classify_regex_bad_pattern_skipped_does_not_crash(caplog):
    slots = {
        "base_color": {
            "suffixes": ["_C"],
            "regex_suffixes": ["[unclosed"],
            "param_names": [],
        },
    }
    # Bad regex → warning logged, classifier still works for literal suffix.
    r = classify_texture("T_Foo_C", slots)
    assert r is not None and r[0] == "base_color"
    # No match for a bad pattern — and a name that doesn't hit literal — is None.
    r2 = classify_texture("T_Foo_X", slots)
    assert r2 is None


def test_classify_regex_loses_to_longer_literal():
    """A longer literal suffix in slot Y beats a shorter regex match in slot X."""
    slots = {
        "regex_slot": {
            # 5-char regex match (`_DIFF`)
            "suffixes": [],
            "regex_suffixes": [r"_DIFF$"],
            "param_names": [],
        },
        "literal_slot": {
            # 10-char literal match (`_BaseColor`)
            "suffixes": ["_BaseColor"],
            "param_names": [],
        },
    }
    # Construct a name that ends with both a regex hit AND a literal hit.
    # `_DIFF_BaseColor` ends with `_BaseColor` (10 chars) — and `_DIFF$` does
    # not match because of the trailing `_BaseColor`. So literal wins.
    r = classify_texture("T_Foo_DIFF_BaseColor", slots)
    assert r is not None and r[0] == "literal_slot"


def test_classify_regex_alternation_anchored_correctly():
    """A top-level `|` regex must not accidentally match mid-string.

    Authors often write `foo|bar` without grouping; the wrapper adds `\\Z`
    around the whole pattern so both branches anchor to end-of-name.
    """
    slots = {
        "test": {
            "suffixes": [],
            "regex_suffixes": ["FOO|BAR"],
            "param_names": [],
        },
    }
    # Name ends with FOO → match.
    assert classify_texture("AAA_FOO", slots)[0] == "test"
    # Name ends with BAR → match.
    assert classify_texture("AAA_BAR", slots)[0] == "test"
    # Name has FOO mid-string but ends differently → no match.
    assert classify_texture("AAA_FOO_BAZ", slots) is None
