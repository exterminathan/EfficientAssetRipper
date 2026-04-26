"""Unit tests for `core.everything` — ctypes.WinDLL is mocked at module level.

These tests cover the Python-side marshalling: query construction, prototype
setup, error mapping, wide-string decoding. The actual Everything64.dll is
not loaded; live DLL behavior is exercised in the opt-in e2e tier.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

import core.everything as everything
from core.everything import (
    EVERYTHING_ERROR_IPC,
    EVERYTHING_OK,
    EverythingError,
    EverythingSDK,
    _normalize_folder,
    get_sdk,
    reset_sdk,
)

pytestmark = [pytest.mark.unit, pytest.mark.windows_only]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_normalize_folder_strips_trailing_separators():
    assert _normalize_folder("C:/Games/Test/") == r"C:\Games\Test"
    assert _normalize_folder(r"C:\Games\Test\\") == r"C:\Games\Test"


def test_normalize_folder_empty_returns_empty():
    assert _normalize_folder("") == ""


def test_normalize_folder_converts_forward_to_back():
    assert _normalize_folder("D:/A/B/C") == r"D:\A\B\C"


# ---------------------------------------------------------------------------
# Mocked DLL infrastructure
# ---------------------------------------------------------------------------

def _make_fake_dll(num_results: int = 0, last_error: int = EVERYTHING_OK,
                   query_ok: bool = True, paths: list[str] | None = None) -> MagicMock:
    """Create a MagicMock that quacks like a loaded WinDLL."""
    paths = paths or []
    dll = MagicMock(name="FakeWinDLL")
    dll.Everything_QueryW.return_value = bool(query_ok)
    dll.Everything_GetNumResults.return_value = num_results
    dll.Everything_GetLastError.return_value = last_error

    # Make GetResultFullPathNameW write into the buffer
    def _get_result(index, buf, size):
        text = paths[index] if 0 <= index < len(paths) else ""
        # ctypes.create_unicode_buffer behaves like a mutable wstring
        buf.value = text
        return len(text)

    dll.Everything_GetResultFullPathNameW.side_effect = _get_result
    return dll


@pytest.fixture
def fake_dll(monkeypatch):
    """Patch ctypes.WinDLL with a factory the test customizes."""
    factory = MagicMock(name="WinDLL")
    monkeypatch.setattr(everything.ctypes, "WinDLL", factory)
    return factory


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_init_raises_everythingerror_when_dll_missing(fake_dll):
    """Every candidate path raises OSError → EverythingError."""
    fake_dll.side_effect = OSError("nope")
    with pytest.raises(EverythingError):
        EverythingSDK()


def test_init_with_explicit_dll_path(fake_dll):
    fake_dll.return_value = _make_fake_dll()
    sdk = EverythingSDK(dll_path=r"C:\custom\Everything64.dll")
    fake_dll.assert_called_with(r"C:\custom\Everything64.dll")
    # Prototypes set on the same instance returned by the factory
    assert sdk._dll is fake_dll.return_value


def test_init_sets_up_function_prototypes(fake_dll):
    dll = _make_fake_dll()
    fake_dll.return_value = dll
    EverythingSDK(dll_path="x")
    # MagicMock auto-creates attributes; .argtypes / .restype assignments
    # are recorded as ordinary attribute writes on the same MagicMock.
    assert dll.Everything_SetSearchW.argtypes == [everything.ctypes.c_wchar_p]
    assert dll.Everything_QueryW.restype == everything.ctypes.c_bool
    assert dll.Everything_GetNumResults.restype == everything.ctypes.c_uint32


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------

def test_search_returns_empty_on_zero_results(fake_dll):
    fake_dll.return_value = _make_fake_dll(num_results=0)
    sdk = EverythingSDK(dll_path="x")
    assert sdk.search("foo") == []


def test_search_decodes_wide_strings_from_pointer(fake_dll):
    paths = [r"C:\X\one.tga", r"D:\Y\two.tga"]
    fake_dll.return_value = _make_fake_dll(num_results=2, paths=paths)
    sdk = EverythingSDK(dll_path="x")
    assert sdk.search("anything") == paths


def test_search_raises_on_query_failure_with_known_error(fake_dll):
    fake_dll.return_value = _make_fake_dll(query_ok=False, last_error=EVERYTHING_ERROR_IPC)
    sdk = EverythingSDK(dll_path="x")
    with pytest.raises(EverythingError, match="Everything service is not running"):
        sdk.search("foo")


def test_search_passes_search_string_to_dll(fake_dll):
    dll = _make_fake_dll()
    fake_dll.return_value = dll
    sdk = EverythingSDK(dll_path="x")
    sdk.search("MyQuery")
    dll.Everything_SetSearchW.assert_called_with("MyQuery")


# ---------------------------------------------------------------------------
# Higher-level helpers
# ---------------------------------------------------------------------------

def test_search_file_with_extension_builds_wfn_query(fake_dll):
    dll = _make_fake_dll()
    fake_dll.return_value = dll
    sdk = EverythingSDK(dll_path="x")
    sdk.search_file("T_Body_C", extension="tga", folder=r"C:\Games\Test")
    # Final call to SetSearchW is the constructed query
    actual_query = dll.Everything_SetSearchW.call_args.args[0]
    assert 'wfn:"T_Body_C.tga"' in actual_query
    assert r'path:"C:\Games\Test"' in actual_query


def test_search_file_without_extension_uses_bare_wfn(fake_dll):
    dll = _make_fake_dll()
    fake_dll.return_value = dll
    sdk = EverythingSDK(dll_path="x")
    sdk.search_file("BareName")
    actual_query = dll.Everything_SetSearchW.call_args.args[0]
    assert actual_query == 'wfn:"BareName"'


def test_find_psk_files_uses_ext_filter(fake_dll):
    dll = _make_fake_dll()
    fake_dll.return_value = dll
    sdk = EverythingSDK(dll_path="x")
    sdk.find_psk_files(folder=r"C:\GameRoot")
    actual_query = dll.Everything_SetSearchW.call_args.args[0]
    assert "ext:psk;pskx" in actual_query
    assert r'path:"C:\GameRoot"' in actual_query


def test_find_props_file_searches_dot_props_dot_txt(fake_dll):
    dll = _make_fake_dll()
    fake_dll.return_value = dll
    sdk = EverythingSDK(dll_path="x")
    sdk.find_props_file("MI_Foo")
    actual_query = dll.Everything_SetSearchW.call_args.args[0]
    assert 'wfn:"MI_Foo.props.txt"' in actual_query


def test_test_connection_handles_query_failure(fake_dll):
    fake_dll.return_value = _make_fake_dll(query_ok=False, last_error=EVERYTHING_ERROR_IPC)
    sdk = EverythingSDK(dll_path="x")
    ok, msg = sdk.test_connection()
    assert ok is False
    assert "Everything service is not running" in msg


def test_test_connection_returns_ok_when_query_succeeds(fake_dll):
    fake_dll.return_value = _make_fake_dll(query_ok=True)
    sdk = EverythingSDK(dll_path="x")
    ok, msg = sdk.test_connection()
    assert ok is True
    assert "connected" in msg.lower()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

def test_get_sdk_returns_singleton(fake_dll):
    fake_dll.return_value = _make_fake_dll()
    reset_sdk()
    a = get_sdk(dll_path="x")
    b = get_sdk(dll_path="x")
    assert a is b
    reset_sdk()


def test_reset_sdk_clears_singleton(fake_dll):
    fake_dll.return_value = _make_fake_dll()
    a = get_sdk(dll_path="x")
    reset_sdk()
    b = get_sdk(dll_path="x")
    assert a is not b
    reset_sdk()
