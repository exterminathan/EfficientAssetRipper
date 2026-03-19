"""Asset browser with tree-based category grouping, search, and filtering.

Replaces the flat table with a Category > Subcategory > Asset tree for
navigating 40K+ assets.  Supports checkbox selection, text filtering,
category filtering, status filtering, and a detail dialog.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import config
from core.asset_scanner import AssetEntry
from gui.widgets import CollapsibleSection, ZoomableTree
import gui.theme as theme


# ---------------------------------------------------------------------------
# Detail dialog
# ---------------------------------------------------------------------------

class AssetDetailDialog(QDialog):
    """Shows full material/texture resolution info for one asset."""

    rescan_requested = Signal(object)  # emits the AssetEntry
    reprocess_requested = Signal(object)  # emits the AssetEntry

    def __init__(self, asset: AssetEntry, parent=None):
        super().__init__(parent)
        self._asset = asset
        self.setWindowTitle(f"Asset Detail \u2014 {asset.name}")
        self.setMinimumSize(700, 500)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{asset.name}</b>"))
        layout.addWidget(QLabel(f"Path: {asset.psk_path}"))

        # Clickable blend path — always shown right after Path
        if asset.blend_path:
            if asset.blend_path.is_file():
                blend_label = QLabel(
                    f'Blend: <a href="open">{asset.blend_path}</a>'
                )

                blend_label.setCursor(Qt.CursorShape.PointingHandCursor)
                blend_label.linkActivated.connect(
                    lambda _: self._open_blend(asset.blend_path)
                )
                layout.addWidget(blend_label)
            else:
                layout.addWidget(QLabel(f"Blend: {asset.blend_path} (file missing)"))

        layout.addWidget(QLabel(f"Category: {asset.category} / {asset.subcategory}"))
        layout.addWidget(QLabel(f"Status: {asset.status_text}"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)

        # Collect all unresolved/missing across all materials for summary section
        all_unresolved: list[tuple[str, str, str]] = []  # (mat_name, tex_name, reason)

        for mat in asset.materials:
            group = CollapsibleSection(
                f"{mat.slot_name}  ({mat.material_name})"
            )
            form = QFormLayout()

            if not mat.props_found:
                form.addRow("", QLabel("\u26a0 Material .props.txt not found"))

            form.addRow("Preset:", QLabel(mat.preset_used))

            for tex in mat.textures:
                label = QLabel(f"\u2713 {tex.path}")
                label.setWordWrap(True)
                form.addRow(f"{tex.slot} ({tex.colorspace}):", label)

            for utex in mat.unresolved:
                label = QLabel(f"\u2717 {utex.texture_name} \u2014 {utex.reason}")
                form.addRow("Missing:", label)
                all_unresolved.append(
                    (mat.material_name, utex.texture_name, utex.reason)
                )

            if mat.bsdf_overrides:
                overrides_str = ", ".join(
                    f"{k}={v}" for k, v in mat.bsdf_overrides.items()
                )
                form.addRow("BSDF overrides:", QLabel(overrides_str))

            group.set_content_layout(form)
            scroll_layout.addWidget(group)

        # Unresolved / missing textures summary section
        if all_unresolved:
            unresolved_group = CollapsibleSection(
                f"Unresolved Textures ({len(all_unresolved)})",
                start_expanded=True,
            )
            uform = QFormLayout()
            for mat_name, tex_name, reason in all_unresolved:
                label = QLabel(f"\u2717 {tex_name} \u2014 {reason}")
                uform.addRow(f"[{mat_name}]:", label)
            unresolved_group.set_content_layout(uform)
            scroll_layout.addWidget(unresolved_group)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        # Bottom button row
        btn_row = QHBoxLayout()

        rescan_btn = QPushButton("Re-scan This Asset")
        rescan_btn.clicked.connect(self._on_rescan)
        btn_row.addWidget(rescan_btn)

        reprocess_btn = QPushButton("Reprocess This Asset")
        reprocess_btn.setToolTip("Re-run Blender processing (overwrites existing .blend)")
        reprocess_btn.clicked.connect(self._on_reprocess)
        btn_row.addWidget(reprocess_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _on_rescan(self):
        self.rescan_requested.emit(self._asset)
        self.close()

    def _on_reprocess(self):
        self.reprocess_requested.emit(self._asset)
        self.close()

    @staticmethod
    def _open_blend(blend_path):
        blender_exe = config.get("blender_exe")
        if blender_exe:
            subprocess.Popen([blender_exe, str(blend_path)])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def _status_colors():
    c = theme.current_scheme()
    return {
        "ready":            QColor(c["status_ready"]),
        "missing_textures": QColor(c["status_warning"]),
        "no_props":         QColor(c["status_failed"]),
        "no_materials":     QColor(c["status_failed"]),
        "processed":        QColor(c["status_processing"]),
    }


_STATUS_LABELS = {
    "all":              "All Statuses",
    "ready":            "Ready",
    "processed":        "Processed",
    "missing_textures": "Missing Textures",
    "no_props":         "No Props",
    "no_materials":     "No Materials",
}


# ---------------------------------------------------------------------------
# Asset browser widget
# ---------------------------------------------------------------------------

class AssetBrowser(QWidget):
    """Tree-based asset browser grouped by category/subcategory."""

    selection_changed = Signal(list)
    rescan_requested = Signal(list)  # list[AssetEntry] to re-resolve
    add_to_queue_requested = Signal(list)  # list[AssetEntry] to queue
    reprocess_requested = Signal(object)  # single AssetEntry to reprocess
    delete_requested = Signal(list)  # list[AssetEntry] to remove from cache

    def __init__(self, parent=None):
        super().__init__(parent)
        self._assets: list[AssetEntry] = []
        self._item_to_idx: dict[int, int] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- Search row: name filter + advanced toggle ---
        search_row = QHBoxLayout()

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by name...")
        self._search.textChanged.connect(self._rebuild_tree)
        search_row.addWidget(self._search, 1)

        self._adv_toggle = QPushButton("Advanced \u25b6")
        self._adv_toggle.setFixedWidth(90)
        self._adv_toggle.setCheckable(True)
        self._adv_toggle.toggled.connect(self._toggle_advanced)
        search_row.addWidget(self._adv_toggle)

        layout.addLayout(search_row)

        # --- Advanced filters (hidden by default) ---
        self._adv_widget = QWidget()
        self._adv_widget.setVisible(False)
        adv_layout = QHBoxLayout(self._adv_widget)
        adv_layout.setContentsMargins(0, 2, 0, 2)

        adv_layout.addWidget(QLabel("Category:"))
        self._cat_filter = QComboBox()
        self._cat_filter.addItem("All Categories")
        self._cat_filter.currentIndexChanged.connect(self._rebuild_tree)
        self._cat_filter.setMinimumWidth(150)
        adv_layout.addWidget(self._cat_filter, 1)

        adv_layout.addWidget(QLabel("Status:"))
        self._status_filter = QComboBox()
        for key, label in _STATUS_LABELS.items():
            self._status_filter.addItem(label, key)
        self._status_filter.currentIndexChanged.connect(self._rebuild_tree)
        self._status_filter.setMinimumWidth(130)
        adv_layout.addWidget(self._status_filter, 1)

        layout.addWidget(self._adv_widget)

        # --- Middle area: expand/collapse + add to queue ---
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()

        self._toggle_expand_btn = QPushButton("Expand All")
        self._expanded = False
        self._toggle_expand_btn.clicked.connect(self._toggle_expand)
        btn_bar.addWidget(self._toggle_expand_btn)

        self._add_queue_btn = QPushButton("Add to Queue")
        self._add_queue_btn.setToolTip("Add checked assets to the processing queue")
        self._add_queue_btn.clicked.connect(self._request_add_to_queue)
        self._add_queue_btn.setProperty("cssClass", "accent")
        btn_bar.addWidget(self._add_queue_btn)

        layout.addLayout(btn_bar)

        # --- Tree ---
        self._tree = ZoomableTree()
        self._tree.setHeaderLabels(["Name", "Status", "Materials", "Blend File", "Path"])
        self._tree.setColumnWidth(0, 350)
        self._tree.setColumnWidth(1, 160)
        self._tree.setColumnWidth(2, 80)
        self._tree.setColumnWidth(3, 200)
        self._tree.setColumnWidth(4, 300)
        self._tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._tree.setAlternatingRowColors(True)
        self._tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tree.doubleClicked.connect(self._on_double_click)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self._tree)

        # --- Status bar ---
        self._status = QLabel("")
        layout.addWidget(self._status)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_assets(self, assets: list[AssetEntry]):
        """Populate the browser with discovered assets."""
        self._assets = assets
        self._populate_category_filter()
        self._rebuild_tree()

    def get_assets(self) -> list[AssetEntry]:
        """Return all loaded assets."""
        return list(self._assets)

    def get_selected_assets(self) -> list[AssetEntry]:
        """Return list of checked (ticked) assets."""
        selected: list[AssetEntry] = []
        self._walk_leaves(self._tree.invisibleRootItem(), selected)
        return selected

    # ------------------------------------------------------------------
    # Internal: build tree
    # ------------------------------------------------------------------

    def _populate_category_filter(self):
        """Fill category dropdown from actual asset data."""
        self._cat_filter.blockSignals(True)
        self._cat_filter.clear()
        self._cat_filter.addItem("All Categories")

        cats: dict[str, int] = defaultdict(int)
        for a in self._assets:
            cats[a.category] += 1

        for cat in sorted(cats):
            self._cat_filter.addItem(f"{cat} ({cats[cat]})", cat)

        self._cat_filter.blockSignals(False)

    def _rebuild_tree(self):
        """Rebuild the tree from current filters."""
        self._tree.clear()
        self._item_to_idx.clear()

        filter_text = self._search.text().lower().strip()
        cat_data = self._cat_filter.currentData()
        status_data = self._status_filter.currentData() or "all"

        groups: dict[str, dict[str, list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )

        visible_count = 0
        ready_count = 0

        for idx, asset in enumerate(self._assets):
            if filter_text and filter_text not in asset.name.lower():
                continue
            if cat_data and asset.category != cat_data:
                continue
            if status_data != "all" and asset.status != status_data:
                continue

            groups[asset.category][asset.subcategory].append(idx)
            visible_count += 1
            if asset.status == "ready":
                ready_count += 1

        self._tree.setUpdatesEnabled(False)

        for cat_name in sorted(groups):
            cat_item = QTreeWidgetItem(self._tree)
            cat_item.setText(0, cat_name)
            sub_count = sum(len(v) for v in groups[cat_name].values())
            cat_item.setText(1, f"({sub_count} assets)")
            cat_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsAutoTristate
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            cat_item.setCheckState(0, Qt.CheckState.Unchecked)
            font = cat_item.font(0)
            font.setBold(True)
            cat_item.setFont(0, font)

            for sub_name in sorted(groups[cat_name]):
                indices = groups[cat_name][sub_name]
                sub_item = QTreeWidgetItem(cat_item)
                sub_item.setText(0, sub_name)
                sub_item.setText(1, f"({len(indices)} assets)")
                sub_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsAutoTristate
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                sub_item.setCheckState(0, Qt.CheckState.Unchecked)

                for asset_idx in indices:
                    asset = self._assets[asset_idx]
                    leaf = QTreeWidgetItem(sub_item)
                    leaf.setText(0, asset.name)
                    leaf.setText(1, asset.status_text)
                    mat_names = ", ".join(m.material_name for m in asset.materials) if asset.materials else ""
                    leaf.setText(2, mat_names)
                    if asset.blend_path:
                        leaf.setText(3, str(asset.blend_path))
                    leaf.setText(4, str(asset.psk_path.parent))

                    leaf.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsUserCheckable
                    )
                    leaf.setCheckState(0, Qt.CheckState.Unchecked)

                    color = _status_colors().get(asset.status)
                    if color:
                        leaf.setForeground(1, color)

                    self._item_to_idx[id(leaf)] = asset_idx

        self._tree.setUpdatesEnabled(True)
        if self._expanded or filter_text:
            self._tree.expandAll()

        self._status.setText(
            f"{visible_count} assets shown ({ready_count} ready) "
            f"\u2014 {len(self._assets)} total"
        )

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _walk_leaves(self, parent: QTreeWidgetItem, out: list[AssetEntry]):
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child.childCount() > 0:
                self._walk_leaves(child, out)
            else:
                if child.checkState(0) == Qt.CheckState.Checked:
                    idx = self._item_to_idx.get(id(child))
                    if idx is not None:
                        out.append(self._assets[idx])

    # ------------------------------------------------------------------
    # Expand / collapse toggle
    # ------------------------------------------------------------------

    def _toggle_expand(self):
        if self._expanded:
            self._tree.collapseAll()
            self._toggle_expand_btn.setText("Expand All")
        else:
            self._tree.expandAll()
            self._toggle_expand_btn.setText("Collapse All")
        self._expanded = not self._expanded

    def _toggle_advanced(self, checked: bool):
        self._adv_widget.setVisible(checked)
        self._adv_toggle.setText("Advanced \u25bc" if checked else "Advanced \u25b6")

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if item is None:
            return
        idx = self._item_to_idx.get(id(item))
        if idx is None:
            return

        asset = self._assets[idx]
        menu = QMenu(self)

        act_delete = QAction("Remove from list / cache", self)
        act_delete.triggered.connect(lambda: self._delete_assets([asset]))
        menu.addAction(act_delete)

        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _delete_assets(self, assets: list[AssetEntry]):
        to_remove = {str(a.psk_path) for a in assets}
        self._assets = [a for a in self._assets if str(a.psk_path) not in to_remove]
        self._populate_category_filter()
        self._rebuild_tree()
        self.delete_requested.emit(assets)

    # ------------------------------------------------------------------
    # Detail view
    # ------------------------------------------------------------------

    def _on_double_click(self, index):
        item = self._tree.currentItem()
        if item is None:
            return
        idx = self._item_to_idx.get(id(item))
        if idx is not None:
            dlg = AssetDetailDialog(self._assets[idx], parent=self)
            dlg.rescan_requested.connect(
                lambda asset: self.rescan_requested.emit([asset])
            )
            dlg.reprocess_requested.connect(
                lambda asset: self.reprocess_requested.emit(asset)
            )
            dlg.exec()

    def _request_add_to_queue(self):
        """Emit add_to_queue_requested with checked assets."""
        selected = self.get_selected_assets()
        if selected:
            self.add_to_queue_requested.emit(selected)

    def refresh_tree(self):
        """Rebuild the tree to reflect updated asset data (e.g. after re-scan)."""
        self._rebuild_tree()
