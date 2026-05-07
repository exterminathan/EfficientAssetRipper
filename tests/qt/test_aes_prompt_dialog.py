"""Tests for the encrypted-archive AES key prompt."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QListWidget

from gui.aes_prompt_dialog import AesPromptDialog

pytestmark = [pytest.mark.qt, pytest.mark.gui]


def test_dialog_prepopulates_existing_keys(qtbot):
    dlg = AesPromptDialog(
        unmounted_count=2,
        archive_names=[
            {"name": "pakchunk0-WindowsNoEditor.pak", "guid": "AA" * 16},
            {"name": "pakchunk1-WindowsNoEditor.pak", "guid": "BB" * 16},
        ],
        existing_keys=[{"label": "Saved", "guid": "CC" * 16, "key": "DEADBEEF"}],
    )
    qtbot.addWidget(dlg)

    keys = dlg.result_keys()
    assert any(k["key"] == "DEADBEEF" for k in keys)


def test_dialog_renders_archive_list_when_provided(qtbot):
    dlg = AesPromptDialog(
        unmounted_count=1,
        archive_names=[{"name": "foo.pak", "guid": "AA" * 16}],
        existing_keys=[],
    )
    qtbot.addWidget(dlg)

    list_widget = dlg.findChild(QListWidget)
    assert list_widget is not None
    assert list_widget.count() == 1
    assert "foo.pak" in list_widget.item(0).text()


def test_dialog_omits_archive_list_when_names_unavailable(qtbot):
    """When the CLI can't surface archive names, the dialog still works."""
    dlg = AesPromptDialog(
        unmounted_count=3,
        archive_names=[],
        existing_keys=[],
    )
    qtbot.addWidget(dlg)

    list_widget = dlg.findChild(QListWidget)
    assert list_widget is None


def test_result_keys_includes_newly_added_row(qtbot):
    dlg = AesPromptDialog(
        unmounted_count=1,
        archive_names=[],
        existing_keys=[],
    )
    qtbot.addWidget(dlg)
    dlg._keys_widget.add_prefilled_row(label="X", guid="00" * 16, key="0xDEADBEEF")

    keys = dlg.result_keys()
    assert any(k["key"] == "0xDEADBEEF" for k in keys)


def test_dialog_prefills_first_archive_guid_when_no_existing_match(qtbot):
    """If the user has no key for the first unmounted archive, pre-fill a row
    with its label/GUID so they only have to paste the hex value."""
    dlg = AesPromptDialog(
        unmounted_count=1,
        archive_names=[{"name": "encrypted.pak", "guid": "AA" * 16}],
        existing_keys=[],
    )
    qtbot.addWidget(dlg)

    table = dlg._keys_widget.table
    # No existing keys + one pre-filled blank-key row → row 0 has the GUID set.
    assert table.rowCount() == 1
    assert table.item(0, 1).text().lower() == "aa" * 16
