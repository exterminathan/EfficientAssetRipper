"""End-to-end smoke tests against a live Everything64.dll + Everything service.

Skipped automatically when the DLL cannot be loaded. Requires the Everything
desktop app to be running (otherwise IPC will fail).
"""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.requires_everything, pytest.mark.windows_only]


def test_sdk_loads_and_test_connection():
    """Load the SDK and call test_connection — must not raise."""
    from core.everything import EverythingSDK, reset_sdk

    reset_sdk()
    dll_path = os.environ.get("EVERYTHING_DLL") or None
    sdk = EverythingSDK(dll_path)
    ok, msg = sdk.test_connection()
    # We only assert the call shape; ok may be False if the service is stopped
    assert isinstance(ok, bool)
    assert isinstance(msg, str)


def test_find_psk_files_returns_list():
    """A live PSK search must return a list (possibly empty)."""
    from core.everything import EverythingSDK, reset_sdk

    reset_sdk()
    dll_path = os.environ.get("EVERYTHING_DLL") or None
    sdk = EverythingSDK(dll_path)
    results = sdk.find_psk_files()
    assert isinstance(results, list)
    # Length-0 is valid (clean machine); length>0 is also valid.
