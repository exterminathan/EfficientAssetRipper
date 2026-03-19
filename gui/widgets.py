"""Shared GUI widgets used across multiple panels."""

from __future__ import annotations

from PySide6.QtCore import Qt, QModelIndex
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget


class ZoomableTree(QTreeWidget):
    """QTreeWidget that supports Ctrl+Scroll zoom and Shift+Click range checkbox toggling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._base_font_size = self.font().pointSize()
        if self._base_font_size <= 0:
            self._base_font_size = 10
        self._last_clicked_item: QTreeWidgetItem | None = None

    # -- Shift+Click range checkbox toggling --

    def mousePressEvent(self, event: QMouseEvent):
        item = self.itemAt(event.pos())
        if (
            item is not None
            and event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            and bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
            and self._last_clicked_item is not None
            and self._last_clicked_item is not item
        ):
            # Determine target state from the clicked item (toggle it)
            new_state = (
                Qt.CheckState.Unchecked
                if item.checkState(0) == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
            items_in_range = self._get_visible_items_between(self._last_clicked_item, item)
            self.blockSignals(True)
            for it in items_in_range:
                if bool(it.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                    it.setCheckState(0, new_state)
            self.blockSignals(False)
            # Emit itemChanged for the range so trackers update
            for it in items_in_range:
                if bool(it.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                    self.itemChanged.emit(it, 0)
            self._last_clicked_item = item
            event.accept()
            return

        if item is not None:
            self._last_clicked_item = item
        super().mousePressEvent(event)

    def _get_visible_items_between(
        self, item_a: QTreeWidgetItem, item_b: QTreeWidgetItem
    ) -> list[QTreeWidgetItem]:
        """Return all visible items between item_a and item_b (inclusive)."""
        all_visible = self._collect_visible_items()
        try:
            idx_a = all_visible.index(item_a)
            idx_b = all_visible.index(item_b)
        except ValueError:
            return [item_b]
        lo, hi = min(idx_a, idx_b), max(idx_a, idx_b)
        return all_visible[lo : hi + 1]

    def _collect_visible_items(self) -> list[QTreeWidgetItem]:
        """Collect all currently visible (expanded) items in display order."""
        result: list[QTreeWidgetItem] = []
        root = self.invisibleRootItem()
        self._walk_visible(root, result)
        return result

    def _walk_visible(self, parent: QTreeWidgetItem, out: list[QTreeWidgetItem]):
        for i in range(parent.childCount()):
            child = parent.child(i)
            out.append(child)
            if child.isExpanded() and child.childCount() > 0:
                self._walk_visible(child, out)

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            font = self.font()
            size = font.pointSize()
            if delta > 0:
                size = min(size + 1, 48)
            elif delta < 0:
                size = max(size - 1, 6)
            font.setPointSize(size)
            self.setFont(font)
            event.accept()
        else:
            super().wheelEvent(event)


class CollapsibleSection(QWidget):
    """A collapsible group with a toggle button showing \u25bc/\u25b6 arrows."""

    def __init__(self, title: str, start_expanded: bool = True, closeable: bool = True, parent=None):
        super().__init__(parent)
        self._expanded = start_expanded
        self._title = title
        self._closeable = closeable

        self._toggle_btn = QPushButton(self._arrow() + "  " + title)
        self._toggle_btn.setProperty("cssClass", "collapsible")
        self._toggle_btn.clicked.connect(self._toggle)

        self._content = QWidget()
        self._content.setVisible(self._expanded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._toggle_btn)
        layout.addWidget(self._content)

        # If not closeable and already expanded, hide the toggle button
        if not self._closeable and self._expanded:
            self._toggle_btn.setVisible(False)

    def set_content_layout(self, content_layout):
        self._content.setLayout(content_layout)

    def _arrow(self) -> str:
        return "\u25bc" if self._expanded else "\u25b6"

    def _toggle(self):
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        self._toggle_btn.setText(self._arrow() + "  " + self._title)
        if not self._closeable and self._expanded:
            self._toggle_btn.setVisible(False)
