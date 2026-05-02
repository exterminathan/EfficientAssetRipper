"""Profile selector toolbar bar — sits at the top of MainWindow."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from core.profile_manager import ProfileManager


class ProfileBar(QWidget):
    """Compact widget: [Profile: ▾ dropdown] [New] [Rename] [Delete]."""

    profile_switch_requested = Signal(str)   # new profile name
    profile_created = Signal(str)            # created profile name
    profile_deleted = Signal(str)            # deleted profile name
    profile_renamed = Signal(str, str)       # old, new

    def __init__(self, manager: ProfileManager, busy_check=None, cancel_fn=None, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._busy_check = busy_check    # callable → bool
        self._cancel_fn = cancel_fn      # callable to cancel running ops
        self._switching = False          # guard re-entrancy
        self._active_profile = ""        # last confirmed profile name

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)

        layout.addWidget(QLabel("Profile:"))

        self._combo = QComboBox()
        self._combo.setMinimumWidth(180)
        self._combo.activated.connect(self._on_combo_activated)
        layout.addWidget(self._combo)

        new_btn = QPushButton("New")
        new_btn.setMinimumWidth(50)
        new_btn.clicked.connect(self._on_new)
        layout.addWidget(new_btn)

        rename_btn = QPushButton("Rename")
        rename_btn.setMinimumWidth(70)
        rename_btn.clicked.connect(self._on_rename)
        layout.addWidget(rename_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.setMinimumWidth(70)
        delete_btn.clicked.connect(self._on_delete)
        layout.addWidget(delete_btn)

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

    # ------------------------------------------------------------------
    # New
    # ------------------------------------------------------------------

    def _on_new(self):
        dlg = _NewProfileDialog(self._manager, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = dlg.profile_name()
            data = dlg.profile_data()
            self._manager.create_profile(name, data)
            self.refresh(select=name)
            self.profile_created.emit(name)

    # ------------------------------------------------------------------
    # Rename
    # ------------------------------------------------------------------

    def _on_rename(self):
        old = self._combo.currentText()
        if not old:
            return

        new, ok = QInputDialog.getText(self, "Rename Profile", "New name:", text=old)
        if not ok or not new or new == old:
            return

        valid, reason = ProfileManager.is_valid_name(new)
        if not valid:
            QMessageBox.warning(self, "Invalid Name", reason)
            return

        if self._manager.profile_exists(new):
            QMessageBox.warning(self, "Duplicate", f"A profile named '{new}' already exists.")
            return

        self._manager.rename_profile(old, new)
        self.refresh(select=new)
        self.profile_renamed.emit(old, new)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _on_delete(self):
        name = self._combo.currentText()
        if not name:
            return

        profiles = self._manager.list_profiles()
        if len(profiles) <= 1:
            QMessageBox.information(self, "Cannot Delete", "You must have at least one profile.")
            return

        reply = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete profile '{name}'?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._manager.delete_profile(name)

        # Switch to next available profile
        remaining = self._manager.list_profiles()
        next_name = remaining[0] if remaining else ""
        self.refresh(select=next_name)
        self.profile_deleted.emit(name)
        if next_name:
            self.profile_switch_requested.emit(next_name)


# ---------------------------------------------------------------------------
# New-profile dialog
# ---------------------------------------------------------------------------

class _NewProfileDialog(QDialog):
    """Dialog for creating a new profile with name + default output dirs."""

    def __init__(self, manager: ProfileManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Game Profile")
        self.setMinimumWidth(450)
        self._manager = manager

        form = QFormLayout(self)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. Satisfactory, Jedi Survivor")
        self._name_edit.textChanged.connect(self._update_suggestions)
        form.addRow("Profile Name:", self._name_edit)

        # Game dir
        game_row = QHBoxLayout()
        self._game_dir_edit = QLineEdit()
        self._game_dir_edit.setPlaceholderText("Path to game .pak folder")
        game_row.addWidget(self._game_dir_edit)
        browse_btn = QPushButton("...")
        browse_btn.setFixedWidth(30)
        browse_btn.clicked.connect(self._browse_game_dir)
        game_row.addWidget(browse_btn)
        form.addRow("Game Dir:", game_row)

        # UE version combo
        self._ue_combo = QComboBox()
        self._ue_combo.addItems([
            "GAME_UE5_0", "GAME_UE5_1", "GAME_UE5_2", "GAME_UE5_3",
            "GAME_UE5_4", "GAME_UE5_5",
            "GAME_UE4_27", "GAME_UE4_26", "GAME_UE4_25",
        ])
        self._ue_combo.setCurrentText("GAME_UE5_4")
        form.addRow("UE Version:", self._ue_combo)

        # Output dirs with auto-suggestion
        self._blend_dir_edit = QLineEdit()
        self._blend_dir_edit.setPlaceholderText("(auto-suggested)")
        form.addRow("Blend Output:", self._blend_dir_edit)

        self._unpack_dir_edit = QLineEdit()
        self._unpack_dir_edit.setPlaceholderText("(auto-suggested)")
        form.addRow("Unpack Output:", self._unpack_dir_edit)

        # Buttons
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._validate_accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _update_suggestions(self, text: str):
        name = text.strip()
        if not name:
            return
        from _base import base_dir
        base = base_dir()
        if not self._blend_dir_edit.text().strip() or self._blend_dir_edit.text().startswith(str(base / "outputs")):
            self._blend_dir_edit.setText(str(base / "outputs" / name))
        if not self._unpack_dir_edit.text().strip() or self._unpack_dir_edit.text().startswith(str(base / "outputs")):
            self._unpack_dir_edit.setText(str(base / "outputs" / name / "unpack"))

    def _browse_game_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Game Content Folder")
        if path:
            self._game_dir_edit.setText(path)

    def _validate_accept(self):
        name = self._name_edit.text().strip()
        valid, reason = ProfileManager.is_valid_name(name)
        if not valid:
            QMessageBox.warning(self, "Invalid Name", reason)
            return
        if self._manager.profile_exists(name):
            QMessageBox.warning(self, "Duplicate", f"A profile named '{name}' already exists.")
            return
        self.accept()

    def profile_name(self) -> str:
        return self._name_edit.text().strip()

    def profile_data(self) -> dict:
        return {
            "game_dir": self._game_dir_edit.text().strip(),
            "ue_version": self._ue_combo.currentText(),
            "unpack_output_dir": self._unpack_dir_edit.text().strip(),
            "blender_output_dir": self._blend_dir_edit.text().strip(),
        }
