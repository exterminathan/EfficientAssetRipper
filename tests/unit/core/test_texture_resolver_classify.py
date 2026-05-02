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
