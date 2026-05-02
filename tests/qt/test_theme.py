"""Tests for `gui.theme.apply` lifecycle hygiene."""

from __future__ import annotations

import pytest

from PySide6.QtWidgets import QApplication

import gui.theme as theme

pytestmark = pytest.mark.qt


@pytest.fixture
def app(qtbot):
    return QApplication.instance() or QApplication([])


def test_apply_no_op_when_scheme_unchanged(app):
    """Re-applying the same scheme should not rebuild the proxy style."""
    theme.apply(app, "Dusk")
    style_a = theme._current_style
    theme.apply(app, "Dusk")
    style_b = theme._current_style
    assert style_a is style_b


def test_apply_swaps_style_when_scheme_changes(app):
    theme.apply(app, "Dusk")
    style_a = theme._current_style
    theme.apply(app, "Bloom")
    style_b = theme._current_style
    assert style_a is not style_b
    assert theme.current_scheme_name() == "Bloom"


def test_fonts_loaded_flag_is_idempotent(app):
    """Calling _load_custom_fonts twice should not re-register fonts."""
    theme._FONTS_LOADED = False
    theme._load_custom_fonts()
    assert theme._FONTS_LOADED is True
    # Second call is a no-op.
    theme._load_custom_fonts()
    assert theme._FONTS_LOADED is True


def test_current_scheme_returns_dict(app):
    theme.apply(app, "Dusk")
    c = theme.current_scheme()
    assert isinstance(c, dict)
    assert "bg_dark" in c
