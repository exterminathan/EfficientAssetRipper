"""Tests for `core.log_redaction` — secret scrubbing helpers."""

from __future__ import annotations

import logging

import pytest

from core.log_redaction import (
    SecretRedactingFilter,
    install_global_redactor,
    redact_sensitive,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# redact_sensitive — pure function
# ---------------------------------------------------------------------------

def test_redact_dict_with_aes_keys_field():
    payload = {
        "game_dir": r"C:\Games\X",
        "aes_keys": [
            {"name": "main", "key": "0x" + "a" * 64},
            {"name": "dlc", "key": "0x" + "b" * 64},
        ],
    }
    out = redact_sensitive(payload)
    assert out["game_dir"] == r"C:\Games\X"
    # Each entry under aes_keys is a dict; only its `key` field is sensitive.
    assert out["aes_keys"][0]["name"] == "main"
    assert "***" in out["aes_keys"][0]["key"]
    assert "a" * 64 not in str(out["aes_keys"])


@pytest.mark.parametrize(
    "field",
    ["aes_key", "aesKey", "AES_KEYS", "password", "TOKEN", "api_secret"],
)
def test_redact_matches_sensitive_field_names_case_insensitively(field):
    out = redact_sensitive({field: "abc123def456"})
    assert out[field] == "***REDACTED***"


def test_redact_leaves_normal_strings_unchanged():
    msg = "exporting C:\\Games\\Foo to D:\\out — done"
    assert redact_sensitive(msg) == msg


def test_redact_masks_inline_long_hex_blob_in_strings():
    """A standalone hex blob ≥ 32 chars looks like an AES key — mask it."""
    blob = "f" * 64
    msg = f"using key {blob} for archive"
    out = redact_sensitive(msg)
    assert blob not in out
    assert "***" in out


def test_redact_does_not_mask_short_hex_strings():
    """An MD5-ish short hash (12 chars) shouldn't get caught."""
    msg = "scan_b6df0cbbd18d.json"
    assert redact_sensitive(msg) == msg


def test_redact_handles_nested_lists():
    payload = [{"key": "secret"}, {"name": "ok"}]
    out = redact_sensitive(payload)
    assert out[0]["key"] == "***REDACTED***"
    assert out[1]["name"] == "ok"


def test_redact_passes_through_non_sensitive_types():
    assert redact_sensitive(42) == 42
    assert redact_sensitive(None) is None
    assert redact_sensitive(True) is True


# ---------------------------------------------------------------------------
# SecretRedactingFilter integration
# ---------------------------------------------------------------------------

def test_filter_scrubs_inline_hex_blob_in_msg(caplog):
    flt = SecretRedactingFilter()
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="key=" + "a" * 64,
        args=(),
        exc_info=None,
    )
    assert flt.filter(record) is True
    assert "a" * 64 not in record.getMessage()
    assert "***" in record.getMessage()


def test_filter_scrubs_dict_arg_with_sensitive_field():
    flt = SecretRedactingFilter()
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="manifest: %s",
        args=({"aes_keys": [{"key": "secret123secret123"}]},),
        exc_info=None,
    )
    flt.filter(record)
    formatted = record.getMessage()
    assert "secret123secret123" not in formatted


def test_install_global_redactor_is_idempotent():
    """Calling install twice should not stack filters."""
    root = logging.getLogger()
    before = len([f for f in root.filters if isinstance(f, SecretRedactingFilter)])
    install_global_redactor()
    install_global_redactor()
    after = len([f for f in root.filters if isinstance(f, SecretRedactingFilter)])
    # Either 0→1 or 1→1, but never 0→2.
    assert after == max(before, 1)
    # Cleanup so other tests aren't affected.
    root.filters = [f for f in root.filters if not isinstance(f, SecretRedactingFilter)]
