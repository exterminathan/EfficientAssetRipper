"""Per-material texture-override editor dialog.

Lets the user pin specific textures to specific slots for individual
materials in a game profile. Edits are made on an in-memory copy of the
profile's ``material_overrides`` dict; the parent dialog persists them via
its own Apply/OK flow.
"""

from __future__ import annotations

import copy
import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.theme import install_combo_click_to_popup
from gui.widgets import PathPicker

log = logging.getLogger(__name__)


class MaterialOverridesDialog(QDialog):
    """Edit per-material overrides in a profile.

    The dialog operates on a deep copy of the supplied overrides dict —
    callers retrieve the edited result via :meth:`result_overrides` after
    the dialog accepts. Cancel discards changes.
    """

    def __init__(
        self,
        overrides: dict,
        presets_data: dict,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Per-Material Overrides")
        self.setMinimumSize(720, 480)

        self._overrides: dict = copy.deepcopy(overrides) if overrides else {}
        self._presets_data = presets_data or {}
        # Material name currently selected in the list (or "" when none).
        self._current_material: str = ""
        # Slot-row widgets for the active material, keyed by slot name.
        self._slot_pickers: dict[str, PathPicker] = {}
        # In-flight construction guard so signal handlers don't fire while
        # we're rebuilding widgets.
        self._building: bool = False

        self._build_ui()
        self._refresh_list()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def result_overrides(self) -> dict:
        """Return the edited overrides dict.

        Call after :meth:`exec` returns ``DialogCode.Accepted``. The result
        is a fresh dict — the caller can mutate it freely.
        """
        # Capture any pending edits to the currently selected material.
        self._capture_current_material()
        return copy.deepcopy(self._overrides)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)

        # Filter row at top.
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Type to filter materials by name…")
        self._filter.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._filter, stretch=1)
        outer.addLayout(filter_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: material list + add/remove.
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self._list, stretch=1)

        list_btns = QHBoxLayout()
        add_btn = QPushButton("Add Material")
        add_btn.clicked.connect(self._on_add_material)
        list_btns.addWidget(add_btn)
        rm_btn = QPushButton("Remove")
        rm_btn.clicked.connect(self._on_remove_material)
        list_btns.addWidget(rm_btn)
        list_btns.addStretch()
        left_layout.addLayout(list_btns)

        splitter.addWidget(left)

        # Right: editor for the selected material.
        self._editor_panel = QWidget()
        self._editor_layout = QVBoxLayout(self._editor_panel)
        self._editor_layout.setContentsMargins(8, 0, 0, 0)
        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        editor_scroll.setWidget(self._editor_panel)
        splitter.addWidget(editor_scroll)
        splitter.setSizes([240, 480])

        outer.addWidget(splitter, stretch=1)

        # Buttons.
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)

        # Empty-state placeholder for the editor side.
        self._editor_placeholder = QLabel(
            "Select a material on the left to edit its overrides,\n"
            "or click Add Material to define one."
        )
        self._editor_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._editor_layout.addWidget(self._editor_placeholder)
        self._editor_layout.addStretch()

    # ------------------------------------------------------------------
    # List & selection
    # ------------------------------------------------------------------

    def _refresh_list(self, select: Optional[str] = None):
        self._list.blockSignals(True)
        self._list.clear()
        flt = self._filter.text().strip().lower()
        names = sorted(self._overrides.keys())
        if flt:
            names = [n for n in names if flt in n.lower()]
        for name in names:
            self._list.addItem(QListWidgetItem(name))
        self._list.blockSignals(False)
        if select:
            for i in range(self._list.count()):
                if self._list.item(i).text() == select:
                    self._list.setCurrentRow(i)
                    return
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        else:
            self._show_placeholder()

    def _on_filter_changed(self, _text: str):
        # Capture pending edits to the current material before re-listing
        # so a filter that hides the current row doesn't lose them.
        self._capture_current_material()
        self._refresh_list()

    def _on_selection_changed(
        self,
        current: Optional[QListWidgetItem],
        previous: Optional[QListWidgetItem],
    ):
        if previous is not None:
            # Capture edits to the previous material.
            self._capture_current_material(name=previous.text())
        if current is None:
            self._current_material = ""
            self._show_placeholder()
            return
        self._current_material = current.text()
        self._show_editor_for(current.text())

    # ------------------------------------------------------------------
    # Editor pane
    # ------------------------------------------------------------------

    def _clear_editor(self):
        # Remove every child widget we added previously.
        while self._editor_layout.count() > 0:
            item = self._editor_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._slot_pickers = {}

    def _show_placeholder(self):
        self._clear_editor()
        ph = QLabel(
            "Select a material on the left to edit its overrides,\n"
            "or click Add Material to define one."
        )
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._editor_layout.addWidget(ph)
        self._editor_layout.addStretch()
        self._editor_placeholder = ph

    def _show_editor_for(self, name: str):
        self._building = True
        try:
            self._clear_editor()
            entry = self._overrides.get(name, {})
            form = QFormLayout()

            # Material name (read-only display).
            form.addRow("Material:", QLabel(name))

            # Preset combo.
            preset_combo = QComboBox()
            preset_names = list(
                (self._presets_data.get("presets") or {}).keys()
            ) or ["default_pbr"]
            preset_combo.addItems(preset_names)
            install_combo_click_to_popup(preset_combo)
            saved = entry.get("preset", "default_pbr") or "default_pbr"
            idx = preset_combo.findText(saved)
            if idx >= 0:
                preset_combo.setCurrentIndex(idx)
            form.addRow("Preset:", preset_combo)
            self._preset_combo = preset_combo

            # Build a slot row per slot in the chosen preset.
            slots = self._slots_for_preset(saved)
            force_textures: dict = entry.get("force_textures", {}) or {}
            for slot_name in slots:
                picker = PathPicker(
                    mode="file",
                    filter_str="Texture (*.tga);;All Files (*)",
                    title=f"Choose texture for {slot_name}",
                )
                picker.setText(force_textures.get(slot_name, ""))
                picker.setPlaceholderText(
                    f"<auto> — leave blank to let the resolver pick {slot_name}"
                )
                form.addRow(f"{slot_name}:", picker)
                self._slot_pickers[slot_name] = picker

            # Update slot rows when the preset changes.
            preset_combo.currentTextChanged.connect(self._on_preset_changed)

            container = QWidget()
            container.setLayout(form)
            self._editor_layout.addWidget(container)
            self._editor_layout.addStretch()
        finally:
            self._building = False

    def _slots_for_preset(self, preset_name: str) -> list[str]:
        presets = self._presets_data.get("presets") or {}
        cfg = presets.get(preset_name) or {}
        slots = cfg.get("texture_slots") or {}
        return list(slots.keys())

    def _on_preset_changed(self, _text: str):
        if self._building:
            return
        # Save the current preset choice, then re-render to swap slot rows.
        self._capture_current_material()
        if self._current_material:
            self._show_editor_for(self._current_material)

    # ------------------------------------------------------------------
    # Capture / mutate state
    # ------------------------------------------------------------------

    def _capture_current_material(self, name: Optional[str] = None):
        """Persist edits from the editor widgets back into ``self._overrides``."""
        target = name if name is not None else self._current_material
        if not target:
            return
        if not self._slot_pickers and not getattr(self, "_preset_combo", None):
            return  # editor isn't showing a material right now
        preset = "default_pbr"
        if hasattr(self, "_preset_combo") and self._preset_combo is not None:
            preset = (
                self._preset_combo.currentText().strip() or "default_pbr"
            )
        force_textures: dict = {}
        for slot, picker in self._slot_pickers.items():
            value = picker.text().strip()
            if value:
                force_textures[slot] = value
        # Even if force_textures is empty, the user may want to record a
        # preset choice for this material — store the entry regardless.
        self._overrides[target] = {
            "preset": preset,
            "force_textures": force_textures,
        }

    def _on_add_material(self):
        from PySide6.QtWidgets import QInputDialog

        self._capture_current_material()
        name, ok = QInputDialog.getText(
            self,
            "Add Material Override",
            "Material name (must match the in-game material exactly):",
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if name in self._overrides:
            self._refresh_list(select=name)
            return
        self._overrides[name] = {"preset": "default_pbr", "force_textures": {}}
        self._refresh_list(select=name)

    def _on_remove_material(self):
        item = self._list.currentItem()
        if item is None:
            return
        name = item.text()
        self._overrides.pop(name, None)
        self._refresh_list()

    def _on_ok(self):
        self._capture_current_material()
        self.accept()
