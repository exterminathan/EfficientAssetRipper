"""Modal dialog that opens when CUE4ParseCLI reports unmounted archives.

Reuses the shared AES key table from gui.profile_dialog so the prompt edits
the same data shape the profile dialog persists. The dialog only collects;
the caller is responsible for writing the merged keys back to the active
profile and re-issuing a mount.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from gui.profile_dialog import AesKeysTableWidget


class AesPromptDialog(QDialog):
    """Ask the user for the AES keys needed to unlock currently unmounted archives.

    The dialog accepts the existing profile keys so the user can fix a wrong
    entry without retyping the rest. ``result_keys()`` returns the merged
    list (existing + newly added rows that have a non-empty hex value).
    """

    def __init__(
        self,
        unmounted_count: int,
        archive_names: list[dict] | None,
        existing_keys: list[dict] | None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("AES Keys Required")
        self.setMinimumSize(620, 480)

        archive_names = archive_names or []
        existing_keys = existing_keys or []

        outer = QVBoxLayout(self)

        # Header — the count is what the CLI guarantees; archive names are
        # best-effort and only shown when the CLI surfaced them.
        header = QLabel(
            f"<b>{unmounted_count} archive(s) need AES keys to mount.</b><br>"
            "Add the matching key(s) below. They will be saved to this profile."
        )
        header.setWordWrap(True)
        outer.addWidget(header)

        if archive_names:
            outer.addWidget(QLabel("Unmounted archives:"))
            self._archive_list = QListWidget()
            self._archive_list.setMaximumHeight(140)
            for entry in archive_names:
                if isinstance(entry, dict):
                    name = entry.get("name", "")
                    guid = entry.get("guid", "")
                else:
                    name = str(entry)
                    guid = ""
                label = name if not guid else f"{name}    [{guid}]"
                self._archive_list.addItem(QListWidgetItem(label))
            outer.addWidget(self._archive_list)
        else:
            self._archive_list = None
            hint = QLabel(
                "<i>Archive names not available from the CLI build — add the "
                "GUIDs you have keys for and click OK.</i>"
            )
            hint.setWordWrap(True)
            hint.setTextFormat(Qt.TextFormat.RichText)
            outer.addWidget(hint)

        outer.addWidget(QLabel("AES keys (label / GUID / hex key):"))
        self._keys_widget = AesKeysTableWidget()
        self._keys_widget.populate(existing_keys)
        # Pre-fill a blank row scoped to the first unmounted GUID so the user
        # has somewhere to paste — only useful when the CLI surfaced names.
        if archive_names and isinstance(archive_names[0], dict):
            first_guid = archive_names[0].get("guid", "")
            if first_guid:
                already_has = any(
                    (k.get("guid") or "").lower() == first_guid.lower()
                    for k in existing_keys
                )
                if not already_has:
                    self._keys_widget.add_prefilled_row(
                        label=archive_names[0].get("name", "Main"),
                        guid=first_guid,
                    )
        outer.addWidget(self._keys_widget)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)

    def result_keys(self) -> list[dict]:
        """Return the merged key list as currently typed in the table."""
        return self._keys_widget.collect()
