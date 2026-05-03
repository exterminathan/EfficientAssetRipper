"""Profile selector toolbar bar — sits at the top of MainWindow.

Now a thin combo + "Manage Profiles..." button. CRUD and per-profile editing
moved to :class:`gui.profile_dialog.ProfileDialog`.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from core.profile_manager import ProfileManager


class ProfileBar(QWidget):
    """Compact widget: [Profile: ▾ dropdown] [Manage Profiles...]."""

    profile_switch_requested = Signal(str)   # new profile name
    manage_requested = Signal()              # user clicked Manage Profiles...

    def __init__(self, manager: ProfileManager, busy_check=None, cancel_fn=None, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._busy_check = busy_check    # callable → bool
        self._cancel_fn = cancel_fn      # callable to cancel running ops
        self._active_profile = ""        # last confirmed profile name

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)

        layout.addWidget(QLabel("Profile:"))

        self._combo = QComboBox()
        self._combo.setMinimumWidth(220)
        self._combo.activated.connect(self._on_combo_activated)
        layout.addWidget(self._combo)

        manage_btn = QPushButton("Manage Profiles...")
        manage_btn.setMinimumWidth(150)
        manage_btn.clicked.connect(self.manage_requested.emit)
        layout.addWidget(manage_btn)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self, select: str | None = None) -> None:
        """Reload the combo from disk.  Optionally select *select*."""
        self._combo.blockSignals(True)
        self._combo.clear()
        names = self._manager.list_profiles()
        self._combo.addItems(names)
        if select and select in names:
            self._combo.setCurrentText(select)
            self._active_profile = select
        elif names and not self._active_profile:
            # No explicit selection and no prior active profile — fall back
            # to the first available so subsequent revert-on-cancel works.
            self._combo.setCurrentText(names[0])
            self._active_profile = names[0]
        self._combo.blockSignals(False)

    def current_profile(self) -> str:
        return self._combo.currentText()

    def set_current(self, name: str) -> None:
        idx = self._combo.findText(name)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        self._active_profile = name

    # ------------------------------------------------------------------
    # Combo activated (user clicked a different profile)
    # ------------------------------------------------------------------

    def _on_combo_activated(self, index: int):
        name = self._combo.itemText(index)
        if not name:
            return

        # Busy check
        if self._busy_check and self._busy_check():
            reply = QMessageBox.question(
                self,
                "Active Operation",
                "An operation is currently running.\n"
                "Cancel it and switch profiles?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                # Revert combo to current active profile
                self._combo.blockSignals(True)
                cur = self._combo.findText(self._active_profile)
                if cur >= 0:
                    self._combo.setCurrentIndex(cur)
                self._combo.blockSignals(False)
                return
            # Cancel the running op
            if self._cancel_fn:
                self._cancel_fn()

        self.profile_switch_requested.emit(name)
