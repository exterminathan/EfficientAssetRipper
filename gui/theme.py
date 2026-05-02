"""Centralised theme engine for EfficientAssetRipper.

Builds a QPalette and a single application-wide QSS stylesheet from a
colour-scheme dict (see ``gui.color_schemes``).  All widget-specific
inline ``setStyleSheet`` calls are replaced by class selectors defined
here so that switching themes only requires calling ``apply()`` once.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from gui.color_schemes import DEFAULT_SCHEME, get_scheme

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Font system
# ---------------------------------------------------------------------------

from _base import base_dir

# Directory where users drop .ttf / .otf font files
FONTS_DIR = base_dir() / "fonts"

# ── Change these two values to use a custom font ─────────────────────
# Set CUSTOM_FONT_FAMILY to the family name that your .ttf/.otf file
# registers (e.g. "Inter", "JetBrains Mono").  Leave as "" to use the
# default system font fallback chain.
CUSTOM_FONT_FAMILY: str = ""
CUSTOM_MONO_FONT_FAMILY: str = ""
# ─────────────────────────────────────────────────────────────────────

_DEFAULT_FONT_FAMILY = "Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
_DEFAULT_MONO_FAMILY = "Cascadia Code, Consolas, Menlo, monospace"

FONT_SIZE = "10pt"
FONT_SIZE_SMALL = "9pt"
FONT_SIZE_LARGE = "12pt"


_FONTS_LOADED = False


def _load_custom_fonts() -> None:
    """Load all .ttf and .otf files from the fonts/ directory (idempotent)."""
    global _FONTS_LOADED
    if _FONTS_LOADED:
        return
    if not FONTS_DIR.is_dir():
        _FONTS_LOADED = True
        return
    for fp in sorted(FONTS_DIR.iterdir()):
        if fp.suffix.lower() in (".ttf", ".otf"):
            fid = QFontDatabase.addApplicationFont(str(fp))
            if fid < 0:
                log.warning("Failed to load font: %s", fp.name)
            else:
                families = QFontDatabase.applicationFontFamilies(fid)
                log.info("Loaded font: %s → %s", fp.name, families)
    _FONTS_LOADED = True


def _font_family() -> str:
    return CUSTOM_FONT_FAMILY if CUSTOM_FONT_FAMILY else _DEFAULT_FONT_FAMILY


def _mono_family() -> str:
    return CUSTOM_MONO_FONT_FAMILY if CUSTOM_MONO_FONT_FAMILY else _DEFAULT_MONO_FAMILY


# ---------------------------------------------------------------------------
# Palette builder
# ---------------------------------------------------------------------------

def _build_palette(c: dict[str, str]) -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(c["bg_dark"]))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(c["text_primary"]))
    pal.setColor(QPalette.ColorRole.Base,            QColor(c["bg_input"]))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(c["bg_mid"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor(c["bg_light"]))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor(c["text_primary"]))
    pal.setColor(QPalette.ColorRole.Text,            QColor(c["text_primary"]))
    pal.setColor(QPalette.ColorRole.Button,          QColor(c["bg_mid"]))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(c["text_primary"]))
    pal.setColor(QPalette.ColorRole.BrightText,      QColor(c["text_bright"]))
    pal.setColor(QPalette.ColorRole.Link,            QColor(c["accent"]))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(c["highlight"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(c["highlight_text"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(c["text_disabled"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c["text_disabled"]))
    return pal


# ---------------------------------------------------------------------------
# QSS builder
# ---------------------------------------------------------------------------

def _build_qss(c: dict[str, str]) -> str:
    ff = _font_family()
    fm = _mono_family()
    return f"""
/* ===== Global ====================================================== */

* {{
    font-family: {ff};
    font-size: {FONT_SIZE};
}}

/* ===== QMainWindow / top-level ===================================== */

QMainWindow {{
    background-color: {c["bg_dark"]};
}}

/* ===== QTabWidget ================================================== */

QTabWidget::pane {{
    border: 1px solid {c["border"]};
    border-radius: 4px;
    background-color: {c["bg_dark"]};
    top: -1px;
}}

QTabBar::tab {{
    background-color: {c["bg_mid"]};
    color: {c["text_secondary"]};
    padding: 6px 16px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    border: 1px solid {c["border"]};
    border-bottom: none;
}}

QTabBar::tab:selected {{
    background-color: {c["bg_dark"]};
    color: {c["text_primary"]};
    border-bottom: 2px solid {c["accent"]};
}}

QTabBar::tab:hover:!selected {{
    background-color: {c["bg_light"]};
    color: {c["text_primary"]};
}}

/* ===== QPushButton ================================================= */

QPushButton {{
    background-color: {c["btn_secondary"]};
    color: {c["btn_text"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    padding: 5px 14px;
    min-height: 20px;
}}

QPushButton:hover {{
    background-color: {c["btn_secondary_hover"]};
    border-color: {c["border_light"]};
}}

QPushButton:pressed {{
    background-color: {c["bg_darkest"]};
}}

QPushButton:disabled {{
    background-color: {c["btn_disabled"]};
    color: {c["text_disabled"]};
    border-color: {c["border"]};
}}

/* ---- Accent buttons (use .accent-btn class) ---- */

QPushButton[cssClass="accent"] {{
    background-color: {c["btn_primary"]};
    color: #ffffff;
    border: none;
}}
QPushButton[cssClass="accent"]:hover {{
    background-color: {c["btn_primary_hover"]};
}}
QPushButton[cssClass="accent"]:disabled {{
    background-color: {c["btn_disabled"]};
    color: {c["text_disabled"]};
}}

/* ---- Success buttons (process / mount) ---- */

QPushButton[cssClass="success"] {{
    background-color: {c["success"]};
    color: #ffffff;
    border: none;
    font-weight: 600;
}}
QPushButton[cssClass="success"]:hover {{
    background-color: {c["success_hover"]};
}}
QPushButton[cssClass="success"]:disabled {{
    background-color: {c["btn_disabled"]};
    color: {c["text_disabled"]};
}}

/* ===== QComboBox =================================================== */

QComboBox {{
    background-color: {c["bg_input"]};
    color: {c["text_primary"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 20px;
}}

QComboBox:hover {{
    border-color: {c["border_light"]};
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {c["text_secondary"]};
    margin-right: 6px;
}}

QComboBox QAbstractItemView {{
    background-color: {c["bg_mid"]};
    color: {c["text_primary"]};
    selection-background-color: {c["highlight"]};
    selection-color: {c["highlight_text"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
}}

/* ===== QLineEdit =================================================== */

QLineEdit {{
    background-color: {c["bg_input"]};
    color: {c["text_primary"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    padding: 4px 8px;
}}

QLineEdit:focus {{
    border-color: {c["accent"]};
}}

/* ===== QTextEdit =================================================== */

QTextEdit {{
    background-color: {c["bg_darkest"]};
    color: {c["text_primary"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    font-family: {fm};
    font-size: {FONT_SIZE_SMALL};
    selection-background-color: {c["accent_muted"]};
}}

/* ===== QTreeWidget / QTreeView ===================================== */

QTreeWidget, QTreeView {{
    background-color: {c["bg_darkest"]};
    color: {c["text_primary"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    alternate-background-color: {c["bg_dark"]};
    outline: none;
}}

QTreeWidget::item, QTreeView::item {{
    padding: 3px 4px;
    border-radius: 2px;
}}

QTreeWidget::item:selected, QTreeView::item:selected {{
    background-color: {c["accent_muted"]};
    color: {c["text_bright"]};
}}

QTreeWidget::item:hover, QTreeView::item:hover {{
    background-color: {c["bg_light"]};
}}

QTreeWidget::branch, QTreeView::branch {{
    background-color: transparent;
}}

/* Branch indicators — solid circle (collapsed) / hollow circle (expanded) */
QTreeWidget::branch:has-children:!has-siblings:closed,
QTreeWidget::branch:closed:has-children:has-siblings,
QTreeView::branch:has-children:!has-siblings:closed,
QTreeView::branch:closed:has-children:has-siblings {{
    image: none;
    border-image: none;
}}

QTreeWidget::branch:open:has-children:!has-siblings,
QTreeWidget::branch:open:has-children:has-siblings,
QTreeView::branch:open:has-children:!has-siblings,
QTreeView::branch:open:has-children:has-siblings {{
    image: none;
    border-image: none;
}}

/* ===== QTableWidget ================================================ */

QTableWidget {{
    background-color: {c["bg_darkest"]};
    color: {c["text_primary"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    gridline-color: {c["border"]};
    alternate-background-color: {c["bg_dark"]};
}}

QTableWidget::item {{
    padding: 3px 6px;
}}

QTableWidget::item:selected {{
    background-color: {c["accent_muted"]};
    color: {c["text_bright"]};
}}

QHeaderView::section {{
    background-color: {c["bg_mid"]};
    color: {c["text_secondary"]};
    padding: 5px 8px;
    border: none;
    border-right: 1px solid {c["border"]};
    border-bottom: 1px solid {c["border"]};
    font-weight: 600;
}}

/* ===== QListWidget ================================================= */

QListWidget {{
    background-color: {c["bg_darkest"]};
    color: {c["text_primary"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    alternate-background-color: {c["bg_dark"]};
    outline: none;
}}

QListWidget::item {{
    padding: 3px 6px;
    border-radius: 2px;
}}

QListWidget::item:selected {{
    background-color: {c["accent_muted"]};
    color: {c["text_bright"]};
}}

QListWidget::item:hover {{
    background-color: {c["bg_light"]};
}}

/* ===== QProgressBar ================================================ */

QProgressBar {{
    background-color: {c["progress_bg"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    text-align: center;
    color: {c["text_primary"]};
    min-height: 18px;
    font-size: {FONT_SIZE_SMALL};
}}

QProgressBar::chunk {{
    background-color: {c["progress_chunk"]};
    border-radius: 5px;
}}

QProgressBar[cssClass="resolving"]::chunk {{
    background-color: {c["warning"]};
}}

/* ===== QSplitter =================================================== */

QSplitter::handle {{
    background-color: {c["border"]};
    margin: 1px;
}}

QSplitter::handle:horizontal {{
    width: 3px;
}}

QSplitter::handle:vertical {{
    height: 3px;
}}

/* ===== QScrollBar ================================================== */

QScrollBar:vertical {{
    background-color: {c["bg_dark"]};
    width: 10px;
    margin: 0;
    border: none;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background-color: {c["border_light"]};
    min-height: 30px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {c["text_disabled"]};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background-color: {c["bg_dark"]};
    height: 10px;
    margin: 0;
    border: none;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
    background-color: {c["border_light"]};
    min-width: 30px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {c["text_disabled"]};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}

/* ===== QStatusBar ================================================== */

QStatusBar {{
    background-color: {c["bg_darkest"]};
    color: {c["text_secondary"]};
    border-top: 1px solid {c["border"]};
    font-size: {FONT_SIZE_SMALL};
    padding: 2px 8px;
}}

/* ===== QMenuBar ==================================================== */

QMenuBar {{
    background-color: {c["bg_dark"]};
    color: {c["text_primary"]};
    border-bottom: 1px solid {c["border"]};
    padding: 2px 0;
}}

QMenuBar::item {{
    padding: 4px 10px;
    border-radius: 3px;
}}

QMenuBar::item:selected {{
    background-color: {c["bg_light"]};
}}

QMenu {{
    background-color: {c["bg_mid"]};
    color: {c["text_primary"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    padding: 4px 0;
}}

QMenu::item {{
    padding: 5px 24px;
    border-radius: 2px;
    margin: 1px 4px;
}}

QMenu::item:selected {{
    background-color: {c["accent_muted"]};
    color: {c["text_bright"]};
}}

QMenu::separator {{
    height: 1px;
    background-color: {c["border"]};
    margin: 4px 8px;
}}

/* ===== QLabel (subdued) ============================================ */

QLabel {{
    color: {c["text_primary"]};
}}

/* ===== QCheckBox / QRadioButton ==================================== */

QCheckBox, QRadioButton {{
    color: {c["text_primary"]};
    spacing: 6px;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {c["border_light"]};
    border-radius: 3px;
    background-color: {c["bg_input"]};
}}

QCheckBox::indicator:checked {{
    background-color: {c["accent"]};
    border-color: {c["accent"]};
}}

QRadioButton::indicator {{
    border-radius: 8px;
}}

QRadioButton::indicator:checked {{
    background-color: {c["accent"]};
    border-color: {c["accent"]};
}}

/* ===== QGroupBox =================================================== */

QGroupBox {{
    border: 1px solid {c["border"]};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 16px;
    font-weight: 600;
    color: {c["text_primary"]};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 10px;
    color: {c["text_secondary"]};
}}

/* ===== QDialog ===================================================== */

QDialog {{
    background-color: {c["bg_dark"]};
}}

/* ===== QToolTip ==================================================== */

QToolTip {{
    background-color: {c["bg_light"]};
    color: {c["text_primary"]};
    border: 1px solid {c["border_light"]};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: {FONT_SIZE_SMALL};
}}

/* ===== QSlider ===================================================== */

QSlider::groove:horizontal {{
    height: 4px;
    background: {c["bg_light"]};
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: {c["accent"]};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}

QSlider::handle:horizontal:hover {{
    background: {c["accent_hover"]};
}}

/* ===== Collapsible section toggle ================================== */

QPushButton[cssClass="collapsible"] {{
    text-align: left;
    font-weight: bold;
    padding: 5px 10px;
    border-radius: 4px;
    background-color: {c["bg_mid"]};
    border: 1px solid {c["border"]};
    color: {c["text_primary"]};
}}

QPushButton[cssClass="collapsible"]:hover {{
    background-color: {c["bg_light"]};
}}
"""


# ---------------------------------------------------------------------------
# Custom branch-indicator style (solid / hollow circles)
# ---------------------------------------------------------------------------

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleOption


class _BranchCircleStyle(QProxyStyle):
    """Draws solid (collapsed) and hollow (expanded) circles for tree
    branch indicators instead of the default arrows."""

    def __init__(self, accent_color: str, base_style=None):
        super().__init__(base_style)
        self._accent = QColor(accent_color)
        self._diameter = 10  # px

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_IndicatorBranch:
            has_children = bool(option.state & QStyle.StateFlag.State_Children)
            if has_children:
                is_open = bool(option.state & QStyle.StateFlag.State_Open)
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                r = option.rect
                d = self._diameter
                cx = r.center().x()
                cy = r.center().y()
                rect = QRectF(cx - d / 2, cy - d / 2, d, d)

                if is_open:
                    # Hollow circle
                    pen = QPen(self._accent, 2.0)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                else:
                    # Solid circle
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(self._accent))

                painter.drawEllipse(rect)
                painter.restore()
                return
        super().drawPrimitive(element, option, painter, widget)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_current_scheme_name: str = DEFAULT_SCHEME
_current_style: "_BranchCircleStyle | None" = None


def current_scheme() -> dict[str, str]:
    """Return the currently active colour-scheme dict."""
    return get_scheme(_current_scheme_name)


def current_scheme_name() -> str:
    return _current_scheme_name


def apply(app: QApplication, scheme_name: str | None = None) -> None:
    """Apply a colour scheme to the running application.

    Sets the Fusion style, the palette, and the global stylesheet.
    Loads custom fonts from the fonts/ directory on first call.
    No-ops if the requested scheme is already active.
    """
    global _current_scheme_name, _current_style
    if scheme_name and scheme_name == _current_scheme_name and _current_style is not None:
        # Already applied — nothing to do, avoid stylesheet thrash.
        return
    if scheme_name:
        _current_scheme_name = scheme_name
    c = get_scheme(_current_scheme_name)

    _load_custom_fonts()

    # Build the new style first so Qt has its own reference before we drop
    # ours — `app.setStyle()` takes ownership and deletes the previous one.
    new_style = _BranchCircleStyle(c["progress_chunk"], "Fusion")
    new_style.setParent(app)      # prevent GC
    app.setStyle(new_style)
    # `_current_style` previously kept a Python ref to the old style; drop it
    # here so the old QProxyStyle isn't kept alive past Qt's lifetime.
    _current_style = new_style
    app.setPalette(_build_palette(c))
    app.setStyleSheet(_build_qss(c))
