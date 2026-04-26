"""Pure-data tests for `gui.color_schemes` (no Qt event loop needed)."""

from __future__ import annotations

import pytest

from gui.color_schemes import (
    DEFAULT_SCHEME,
    SCHEME_KEYS,
    SCHEMES,
    get_scheme,
    list_scheme_names,
    register_custom_scheme,
)

pytestmark = pytest.mark.qt   # grouped here for organization; no qtbot needed


def test_default_scheme_present():
    assert DEFAULT_SCHEME in SCHEMES


def test_get_scheme_falls_back_to_default():
    fallback = get_scheme("totally-unknown-name")
    assert fallback == SCHEMES[DEFAULT_SCHEME]


def test_get_scheme_named_returns_correct_dict():
    assert get_scheme(DEFAULT_SCHEME) is SCHEMES[DEFAULT_SCHEME]


def test_register_custom_scheme_fills_missing_keys():
    register_custom_scheme("Custom_Test", {"accent": "#abcdef"})
    custom = SCHEMES["Custom_Test"]
    # Every base key must still be present
    for k in SCHEME_KEYS:
        assert k in custom
    assert custom["accent"] == "#abcdef"


def test_list_scheme_names_sorted():
    names = list_scheme_names()
    assert names == sorted(names)
