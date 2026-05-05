"""Color scheme picker / customizer dialog.

Spun out of ``gui.settings_panel`` so the swatch grid has room to breathe
without a nested ``QScrollArea`` clipping it inside the Settings dialog.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import config
from gui.color_schemes import (
    SCHEME_KEYS,
    SCHEMES,
    get_scheme,
    list_scheme_names,
    register_custom_scheme,
)
import gui.theme as theme


_BUILTIN = {"Dusk", "Bloom", "Slate", "Midnight"}


class ColorSchemeDialog(QDialog):
    """Pick a built-in scheme or customize a user scheme's swatches."""

    scheme_changed = Signal(str)  # active scheme name on accept

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Color Scheme")
        self.setMinimumSize(720, 600)

        self._custom_colors: dict[str, str] = {}
        self._swatch_buttons: dict[str, QPushButton] = {}

        outer = QVBoxLayout(self)

        # ── Scheme picker row ─────────────────────────────────────────
        scheme_row = QHBoxLayout()
        scheme_row.addWidget(QLabel("Scheme:"))
        self._scheme_combo = QComboBox()
        self._scheme_combo.addItems(list_scheme_names())
        current = config.get("color_scheme") or theme.current_scheme_name()
        if current in [self._scheme_combo.itemText(i) for i in range(self._scheme_combo.count())]:
            self._scheme_combo.setCurrentText(current)
        self._scheme_combo.currentTextChanged.connect(self._on_scheme_changed)
        scheme_row.addWidget(self._scheme_combo)

        self._new_scheme_btn = QPushButton("New Custom…")
        self._new_scheme_btn.clicked.connect(self._new_custom_scheme)
        scheme_row.addWidget(self._new_scheme_btn)

        self._delete_scheme_btn = QPushButton("Delete Custom")
        self._delete_scheme_btn.clicked.connect(self._delete_custom_scheme)
        scheme_row.addWidget(self._delete_scheme_btn)

        scheme_row.addStretch()
        outer.addLayout(scheme_row)

        # ── Swatch grid (no max-height clamp) ─────────────────────────
        self._color_scroll = QScrollArea()
        self._color_scroll.setWidgetResizable(True)
        self._color_grid_widget = QWidget()
        self._color_grid = QGridLayout(self._color_grid_widget)
        self._color_grid.setContentsMargins(4, 4, 4, 4)
        self._color_grid.setSpacing(4)
        self._color_scroll.setWidget(self._color_grid_widget)
        outer.addWidget(self._color_scroll, stretch=1)

        # ── Buttons ───────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        # Seed the grid with the currently selected scheme's colors
        self._custom_colors = dict(get_scheme(self._scheme_combo.currentText()))
        self._populate_color_grid()
        self._update_delete_button()

    # ------------------------------------------------------------------
    # Selection & grid population
    # ------------------------------------------------------------------

    def _on_scheme_changed(self, name: str):
        self._custom_colors = dict(get_scheme(name))
        self._populate_color_grid()
        self._update_delete_button()

    def _populate_color_grid(self):
        while self._color_grid.count():
            item = self._color_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._swatch_buttons.clear()

        scheme_name = self._scheme_combo.currentText()
        colors = dict(get_scheme(scheme_name))
        if self._custom_colors:
            colors.update(self._custom_colors)
        else:
            self._custom_colors = dict(colors)

        editable = scheme_name not in _BUILTIN

        # Pull the swatch border from the active theme so it stays visible
        # against any background — built-in or user-defined.
        border_hex = theme.current_scheme().get("border_light", "#666666")

        cols = 4
        for idx, key in enumerate(SCHEME_KEYS):
            row, col = divmod(idx, cols)
            container = QHBoxLayout()
            lbl = QLabel(key.replace("_", " ").title())
            lbl.setFixedWidth(120)
            container.addWidget(lbl)

            btn = QPushButton()
            btn.setFixedSize(36, 22)
            hex_color = colors.get(key, "#888888")
            btn.setStyleSheet(
                f"background-color: {hex_color}; border: 1px solid {border_hex}; border-radius: 3px;"
            )
            btn.setToolTip(hex_color)
            if editable:
                btn.clicked.connect(lambda checked=False, k=key: self._pick_color(k))
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                btn.setEnabled(False)
            self._swatch_buttons[key] = btn
            container.addWidget(btn)

            wrapper = QWidget()
            wrapper.setLayout(container)
            self._color_grid.addWidget(wrapper, row, col)

    def _pick_color(self, key: str):
        current = QColor(self._custom_colors.get(key, "#888888"))
        color = QColorDialog.getColor(current, self, f"Pick colour for {key}")
        if color.isValid():
            hex_val = color.name()
            self._custom_colors[key] = hex_val
            btn = self._swatch_buttons.get(key)
            if btn:
                border_hex = theme.current_scheme().get("border_light", "#666666")
                btn.setStyleSheet(
                    f"background-color: {hex_val}; border: 1px solid {border_hex}; border-radius: 3px;"
                )
                btn.setToolTip(hex_val)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def _new_custom_scheme(self):
        name, ok = QInputDialog.getText(self, "New Custom Scheme", "Scheme name:")
        if not ok or not name:
            return
        name = name.strip()
        if name in SCHEMES:
            QMessageBox.warning(self, "Duplicate", f"A scheme named '{name}' already exists.")
            return

        base = dict(get_scheme(self._scheme_combo.currentText()))
        register_custom_scheme(name, base)
        self._save_custom_schemes_to_config()

        self._scheme_combo.blockSignals(True)
        self._scheme_combo.clear()
        self._scheme_combo.addItems(list_scheme_names())
        self._scheme_combo.setCurrentText(name)
        self._scheme_combo.blockSignals(False)

        self._custom_colors = dict(base)
        self._populate_color_grid()
        self._update_delete_button()

    def _delete_custom_scheme(self):
        name = self._scheme_combo.currentText()
        if name in _BUILTIN:
            return
        reply = QMessageBox.question(
            self, "Delete Scheme",
            f"Delete custom scheme '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        SCHEMES.pop(name, None)
        self._save_custom_schemes_to_config()

        self._scheme_combo.blockSignals(True)
        self._scheme_combo.clear()
        self._scheme_combo.addItems(list_scheme_names())
        self._scheme_combo.setCurrentText("Dusk")
        self._scheme_combo.blockSignals(False)

        self._on_scheme_changed("Dusk")

    def _update_delete_button(self):
        name = self._scheme_combo.currentText()
        self._delete_scheme_btn.setEnabled(name not in _BUILTIN)

    def _save_custom_schemes_to_config(self):
        custom = {sname: scolors for sname, scolors in SCHEMES.items() if sname not in _BUILTIN}
        config.set("custom_schemes", json.dumps(custom))

    # ------------------------------------------------------------------
    # Accept
    # ------------------------------------------------------------------

    def _accept(self):
        scheme_name = self._scheme_combo.currentText()
        config.set("color_scheme", scheme_name)

        if self._custom_colors and scheme_name not in _BUILTIN:
            register_custom_scheme(scheme_name, self._custom_colors)
            self._save_custom_schemes_to_config()

        app = QApplication.instance()
        if app:
            theme.apply(app, scheme_name)

        self.scheme_changed.emit(scheme_name)
        self.accept()
