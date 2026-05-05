"""Tests for `gui.color_scheme_dialog.ColorSchemeDialog`.

Covers the live-preview checkbox and reset-to-default button — the bits
that touch the running app's theme via ``theme.apply()``.
"""

from __future__ import annotations

import pytest

import config
import gui.theme as theme
from gui.color_schemes import DEFAULT_SCHEME, SCHEMES, register_custom_scheme
from gui.color_scheme_dialog import ColorSchemeDialog, _PREVIEW_SCHEME_NAME

pytestmark = [pytest.mark.qt, pytest.mark.gui]


@pytest.fixture
def custom_scheme(mock_qsettings):
    """Register a fresh custom scheme that tests can mutate freely."""
    register_custom_scheme("UnitTestScheme", dict(SCHEMES[DEFAULT_SCHEME]))
    config.set("color_scheme", "UnitTestScheme")
    yield "UnitTestScheme"
    SCHEMES.pop("UnitTestScheme", None)
    SCHEMES.pop(_PREVIEW_SCHEME_NAME, None)


# ---------------------------------------------------------------------------
# Live preview
# ---------------------------------------------------------------------------

def test_dialog_constructs(qtbot, mock_qsettings):
    dlg = ColorSchemeDialog()
    qtbot.addWidget(dlg)
    assert dlg.windowTitle() == "Color Scheme"
    # Live checkbox starts off so existing-flow tests don't break.
    assert dlg._live_chk.isChecked() is False


def test_live_toggle_applies_current_scheme(qtbot, mock_qsettings, monkeypatch):
    """Toggling Live ON must call theme.apply with the current scheme."""
    calls: list = []
    monkeypatch.setattr(theme, "apply", lambda app, name=None: calls.append(name))

    dlg = ColorSchemeDialog()
    qtbot.addWidget(dlg)
    dlg._scheme_combo.setCurrentText("Slate")
    calls.clear()
    dlg._live_chk.setChecked(True)
    assert calls and calls[-1] == "Slate"


def test_picking_color_with_live_off_does_not_apply_theme(qtbot, custom_scheme, monkeypatch):
    calls: list = []
    monkeypatch.setattr(theme, "apply", lambda app, name=None: calls.append(name))

    dlg = ColorSchemeDialog()
    qtbot.addWidget(dlg)
    dlg._scheme_combo.setCurrentText(custom_scheme)
    calls.clear()
    # Simulate a colour edit without going through QColorDialog.
    dlg._custom_colors["accent"] = "#abcdef"
    # _live_chk is off — no theme.apply should fire.
    assert calls == []


def test_picking_color_with_live_on_registers_preview_scheme(qtbot, custom_scheme, monkeypatch):
    """Live edits land under ``__preview__`` so cancelling can revert cleanly."""
    calls: list = []
    monkeypatch.setattr(theme, "apply", lambda app, name=None: calls.append(name))

    dlg = ColorSchemeDialog()
    qtbot.addWidget(dlg)
    dlg._scheme_combo.setCurrentText(custom_scheme)
    dlg._live_chk.setChecked(True)
    calls.clear()
    dlg._custom_colors["accent"] = "#abcdef"
    dlg._apply_preview()  # what _pick_color does after a real picker

    assert _PREVIEW_SCHEME_NAME in SCHEMES
    assert SCHEMES[_PREVIEW_SCHEME_NAME]["accent"] == "#abcdef"
    assert calls and calls[-1] == _PREVIEW_SCHEME_NAME


def test_cancel_reverts_to_original_scheme(qtbot, custom_scheme, monkeypatch):
    """Cancel should re-apply the originally-active scheme name."""
    calls: list = []
    monkeypatch.setattr(theme, "apply", lambda app, name=None: calls.append(name))

    dlg = ColorSchemeDialog()
    qtbot.addWidget(dlg)
    dlg._live_chk.setChecked(True)
    dlg._scheme_combo.setCurrentText("Slate")
    calls.clear()
    dlg.reject()

    # Final apply must put us back on the originally-active scheme.
    assert calls and calls[-1] == custom_scheme
    # Preview entry must be cleaned up.
    assert _PREVIEW_SCHEME_NAME not in SCHEMES


def test_accept_persists_scheme_and_clears_preview(qtbot, custom_scheme, monkeypatch):
    monkeypatch.setattr(theme, "apply", lambda app, name=None: None)

    dlg = ColorSchemeDialog()
    qtbot.addWidget(dlg)
    dlg._live_chk.setChecked(True)
    dlg._scheme_combo.setCurrentText(custom_scheme)
    dlg._custom_colors["accent"] = "#112233"
    dlg._apply_preview()
    dlg._accept()

    # OK persists the chosen scheme.
    assert config.get("color_scheme") == custom_scheme
    # And does not leak the preview-only sentinel into the registry.
    assert _PREVIEW_SCHEME_NAME not in SCHEMES


# ---------------------------------------------------------------------------
# Reset-to-default
# ---------------------------------------------------------------------------

def test_reset_button_disabled_for_builtin(qtbot, mock_qsettings):
    dlg = ColorSchemeDialog()
    qtbot.addWidget(dlg)
    dlg._scheme_combo.setCurrentText("Dusk")
    assert dlg._reset_btn.isEnabled() is False


def test_reset_button_enabled_for_custom(qtbot, custom_scheme):
    dlg = ColorSchemeDialog()
    qtbot.addWidget(dlg)
    dlg._scheme_combo.setCurrentText(custom_scheme)
    assert dlg._reset_btn.isEnabled() is True


def test_reset_replaces_custom_colors_with_defaults(qtbot, custom_scheme, monkeypatch):
    """Confirming the reset prompt must rewrite the scheme to defaults."""
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Yes),
    )

    # Mutate the custom scheme so we can prove the reset overwrote it.
    SCHEMES[custom_scheme]["accent"] = "#deadbe"

    dlg = ColorSchemeDialog()
    qtbot.addWidget(dlg)
    dlg._scheme_combo.setCurrentText(custom_scheme)
    dlg._reset_to_default()

    # After reset, the scheme matches the default scheme's accent — not
    # the mutated value.
    assert SCHEMES[custom_scheme]["accent"] == SCHEMES[DEFAULT_SCHEME]["accent"]
    assert dlg._custom_colors["accent"] == SCHEMES[DEFAULT_SCHEME]["accent"]


def test_reset_does_nothing_when_user_says_no(qtbot, custom_scheme, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **kw: QMessageBox.StandardButton.No),
    )

    SCHEMES[custom_scheme]["accent"] = "#deadbe"

    dlg = ColorSchemeDialog()
    qtbot.addWidget(dlg)
    dlg._scheme_combo.setCurrentText(custom_scheme)
    dlg._reset_to_default()

    assert SCHEMES[custom_scheme]["accent"] == "#deadbe"
