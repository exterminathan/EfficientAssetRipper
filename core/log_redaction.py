"""Helpers for scrubbing AES keys and other secrets from log output.

The intent is defense-in-depth: AES keys live in profiles, get passed
across the CUE4ParseCLI IPC boundary, and may end up in `repr()` of the
manifest dict that is logged when Blender runs. The redactor strips
anything that looks like a secret before the log record is formatted.

Two ways to use this module:

1. As a logging filter — :class:`SecretRedactingFilter` walks ``record.args``
   and ``record.msg`` and rewrites mappings/sequences in place.
2. As a callable on the caller's data — :func:`redact_sensitive` takes a
   dict/list/string and returns a deep-copied, redacted version.

Sensitivity is judged on field name (sub-string match against a small
allow-list) plus shape: a hex blob ≥ 32 chars in any string is masked
inline, since AES keys can't be reliably distinguished from non-secrets
by value alone.
"""

from __future__ import annotations

import logging
import re
from typing import Any

# Words whose presence in a field name marks the value as sensitive. The
# match is a case-insensitive substring search — "monkey" would technically
# trip "key", but our schemas don't carry such names, and over-redaction is
# strictly safer than under-redaction.
_SENSITIVE_SUBSTRINGS: tuple[str, ...] = (
    "key", "aes", "password", "token", "secret",
)

# Hex strings >= 32 chars look like an AES-128/256 key — mask them even if
# they appear inline in a free-form log message.
_HEX_KEY_RE = re.compile(r"\b(?:0x)?[A-Fa-f0-9]{32,128}\b")

_REDACTED = "***REDACTED***"


def _is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    lc = key.lower()
    return any(s in lc for s in _SENSITIVE_SUBSTRINGS)


def redact_sensitive(value: Any) -> Any:
    """Return a deep-redacted copy of *value*.

    Behavior:

    - In a mapping, when the key matches the sensitive-name pattern *and*
      the value is a primitive (str/int/float/bool/None), the value is
      replaced with ``"***REDACTED***"``. When the value is a container,
      we recurse so descriptive labels (e.g. ``aes_keys[0]["name"]``)
      survive while the actual key bytes are still masked.
    - Strings get inline hex blobs ≥ 32 chars masked.
    - Other types pass through unchanged.
    """
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if _is_sensitive_key(k):
                if isinstance(v, (dict, list, tuple)):
                    out[k] = redact_sensitive(v)
                else:
                    out[k] = _REDACTED
            else:
                out[k] = redact_sensitive(v)
        return out
    if isinstance(value, list):
        return [redact_sensitive(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(v) for v in value)
    if isinstance(value, str):
        return _HEX_KEY_RE.sub(_REDACTED, value)
    return value


class SecretRedactingFilter(logging.Filter):
    """A ``logging.Filter`` that scrubs args + the formatted message in place.

    Attach it to a handler to apply project-wide. Modifying records here
    rather than in formatters keeps the redaction visible to any handler
    that follows.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact_sensitive(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(redact_sensitive(a) for a in record.args)
        if isinstance(record.msg, str):
            record.msg = _HEX_KEY_RE.sub(_REDACTED, record.msg)
        return True


def install_global_redactor() -> None:
    """Attach :class:`SecretRedactingFilter` to the root logger.

    Idempotent — calling twice does not stack filters.
    """
    root = logging.getLogger()
    for f in root.filters:
        if isinstance(f, SecretRedactingFilter):
            return
    root.addFilter(SecretRedactingFilter())
