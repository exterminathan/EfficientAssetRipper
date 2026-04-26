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
