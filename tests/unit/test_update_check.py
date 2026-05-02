"""Tests for `core.update_check` — semver compare, cache, silent failure."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core import update_check
from core.update_check import (
    UpdateInfo,
    _is_safe_release_url,
    _is_valid_tag,
    check_for_update,
    is_newer,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Semver comparison
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("0.5.1", "0.5.0", True),
        ("v0.5.1", "0.5.0", True),       # tag prefix tolerated
        ("1.0.0", "0.99.99", True),
        ("0.5.0", "0.5.0", False),
        ("0.5.0", "0.5.1", False),
        ("0.4.9", "0.5.0", False),
        ("0.5.0-rc1", "0.5.0", False),   # 0.5.0 == 0.5.0 numerically; rc loses
        ("0.6.0-rc1", "0.5.0", True),
    ],
)
def test_is_newer(latest, current, expected):
    assert is_newer(latest, current) is expected


# ---------------------------------------------------------------------------
# Tag/URL validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v0.5.0", True),
        ("0.5.0", True),
        ("0.5.0-rc1", True),
        ("0.5.0-beta.2", True),
        ("v9.9.9", True),
        # Reject anything that isn't shaped like a version.
        ("", False),
        ("javascript:alert(1)", False),
        ("'; DROP TABLE--", False),
        ("v0.5.0\n<script>", False),
        # Length cap (max 64).
        ("v" + "1." * 40, False),
        # Spaces never appear in a real tag.
        ("v 0.5.0", False),
    ],
)
def test_is_valid_tag(tag, expected):
    assert _is_valid_tag(tag) is expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/owner/repo/releases/tag/v1", True),
        ("https://github.com/owner/repo", True),
        # Wrong scheme — block javascript:, http:, file:.
        ("http://github.com/owner/repo", False),
        ("javascript:alert(1)", False),
        ("file:///etc/passwd", False),
        # Wrong host (subdomain or unrelated domain).
        ("https://evil.com/x", False),
        ("https://gist.github.com/x", False),
        ("", False),
        ("not-a-url", False),
    ],
)
def test_is_safe_release_url(url, expected):
    assert _is_safe_release_url(url) is expected


# ---------------------------------------------------------------------------
# check_for_update — patched _build_opener
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_cache(monkeypatch, tmp_path):
    """Redirect the cache file to tmp_path."""
    cache_file = tmp_path / "update_check.json"
    monkeypatch.setattr(update_check, "_cache_path", lambda: cache_file)
    return cache_file


def _mock_response(payload: dict, content_length: str | None = None) -> MagicMock:
    """Build a context-manager response returning JSON bytes.

    Mimics the urllib.request.urlopen contract:
    - context-manager protocol
    - .read(n) returns bytes
    - .headers.get(name) returns the configured Content-Length (or None)
    """
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")

    headers = MagicMock()
    headers.get.return_value = content_length
    resp.headers = headers

    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = None
    return cm


def _patch_opener(monkeypatch, response_cm):
    """Replace `_build_opener()` so the test controls the network response."""
    opener = MagicMock()
    opener.open.return_value = response_cm
    monkeypatch.setattr(update_check, "_build_opener", lambda: opener)
    return opener


def test_check_for_update_returns_info_when_newer(monkeypatch, isolated_cache):
    cm = _mock_response({
        "tag_name": "v0.6.0",
        "html_url": "https://github.com/owner/repo/releases/tag/v0.6.0",
    })
    _patch_opener(monkeypatch, cm)

    info = check_for_update(repo="some/repo", current="0.5.0", now=1_000_000)
    assert isinstance(info, UpdateInfo)
    assert info.latest == "v0.6.0"
    assert info.current == "0.5.0"
    assert info.is_newer is True
    assert info.release_url == "https://github.com/owner/repo/releases/tag/v0.6.0"


def test_check_for_update_returns_info_when_equal(monkeypatch, isolated_cache):
    cm = _mock_response({
        "tag_name": "v0.5.0",
        "html_url": "https://github.com/owner/repo/releases/tag/v0.5.0",
    })
    _patch_opener(monkeypatch, cm)

    info = check_for_update(repo="some/repo", current="0.5.0", now=1_000_000)
    assert info is not None
    assert info.is_newer is False


def test_check_for_update_silent_on_network_error(monkeypatch, isolated_cache):
    opener = MagicMock()
    opener.open.side_effect = urllib.error.URLError("nope")
    monkeypatch.setattr(update_check, "_build_opener", lambda: opener)

    # Must not raise
    info = check_for_update(repo="some/repo", current="0.5.0", now=1_000_000)
    assert info is None


def test_check_for_update_silent_on_malformed_json(monkeypatch, isolated_cache):
    resp = MagicMock()
    resp.read.return_value = b"not json"
    headers = MagicMock()
    headers.get.return_value = None
    resp.headers = headers
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = None
    _patch_opener(monkeypatch, cm)

    info = check_for_update(repo="some/repo", current="0.5.0", now=1_000_000)
    assert info is None


def test_check_for_update_caches_response(monkeypatch, isolated_cache: Path):
    """Second call within 24h must skip the network and read from cache."""
    cm = _mock_response({
        "tag_name": "v0.7.0",
        "html_url": "https://github.com/owner/repo/releases/tag/v0.7.0",
    })
    opener = MagicMock()
    call_counter = {"n": 0}

    def _open(*a, **k):
        call_counter["n"] += 1
        return cm

    opener.open.side_effect = _open
    monkeypatch.setattr(update_check, "_build_opener", lambda: opener)

    info1 = check_for_update(repo="some/repo", current="0.5.0", now=1_000_000)
    assert info1 is not None
    assert call_counter["n"] == 1
    assert isolated_cache.is_file()

    # Second call inside the 24h TTL window — must NOT hit the network.
    info2 = check_for_update(repo="some/repo", current="0.5.0", now=1_000_000 + 60)
    assert info2 is not None
    assert info2.latest == "v0.7.0"
    assert call_counter["n"] == 1, "network was called twice; cache was bypassed"


def test_check_for_update_refreshes_when_cache_expired(monkeypatch, isolated_cache: Path):
    cm1 = _mock_response({
        "tag_name": "v0.7.0",
        "html_url": "https://github.com/owner/repo/r/v0.7.0",
    })
    cm2 = _mock_response({
        "tag_name": "v0.8.0",
        "html_url": "https://github.com/owner/repo/r/v0.8.0",
    })
    responses = iter([cm1, cm2])
    opener = MagicMock()
    opener.open.side_effect = lambda *a, **k: next(responses)
    monkeypatch.setattr(update_check, "_build_opener", lambda: opener)

    t0 = 1_000_000
    info1 = check_for_update(repo="r", current="0.5.0", now=t0)
    assert info1 is not None and info1.latest == "v0.7.0"

    # 25h later: cache expired, must re-fetch.
    t1 = t0 + 25 * 60 * 60
    info2 = check_for_update(repo="r", current="0.5.0", now=t1)
    assert info2 is not None and info2.latest == "v0.8.0"


def test_check_for_update_cache_refreshes_when_repo_changes(monkeypatch, isolated_cache: Path):
    cm = _mock_response({
        "tag_name": "v9.9.9",
        "html_url": "https://github.com/x/y/r/v9.9.9",
    })
    opener = MagicMock()
    call_counter = {"n": 0}

    def _open(*a, **k):
        call_counter["n"] += 1
        return cm

    opener.open.side_effect = _open
    monkeypatch.setattr(update_check, "_build_opener", lambda: opener)

    check_for_update(repo="repoA", current="0.5.0", now=1_000_000)
    check_for_update(repo="repoB", current="0.5.0", now=1_000_000 + 1)
    assert call_counter["n"] == 2, "different repo should bypass cache"


def test_cache_file_corrupt_falls_back_to_fetch(monkeypatch, isolated_cache: Path):
    isolated_cache.write_text("{not json", encoding="utf-8")

    cm = _mock_response({
        "tag_name": "v0.6.0",
        "html_url": "https://github.com/x/y/r/v0.6.0",
    })
    _patch_opener(monkeypatch, cm)

    info = check_for_update(repo="r", current="0.5.0", now=1_000_000)
    assert info is not None
    assert info.latest == "v0.6.0"


# ---------------------------------------------------------------------------
# Hardening: response-size cap, suspicious tag rejection, off-host URL stripping
# ---------------------------------------------------------------------------

def test_response_size_cap_rejects_oversized_content_length(monkeypatch, isolated_cache):
    """A massive Content-Length must abort before the body is read."""
    cm = _mock_response(
        {"tag_name": "v0.6.0", "html_url": "https://github.com/x/y"},
        content_length=str(10 * 1024 * 1024),  # 10 MB — way over the 256 KB cap
    )
    _patch_opener(monkeypatch, cm)

    info = check_for_update(repo="r", current="0.5.0", now=1_000_000)
    assert info is None


def test_response_body_is_read_with_byte_cap(monkeypatch, isolated_cache):
    """`.read()` must be called with the 64 KB hard cap."""
    cm = _mock_response({"tag_name": "v0.6.0", "html_url": "https://github.com/x/y"})
    _patch_opener(monkeypatch, cm)

    check_for_update(repo="r", current="0.5.0", now=1_000_000)
    # The response's read() should have been invoked with a positive size cap.
    resp = cm.__enter__.return_value
    resp.read.assert_called_once()
    cap = resp.read.call_args.args[0]
    assert isinstance(cap, int) and cap > 0
    assert cap <= 64 * 1024


def test_suspicious_tag_name_rejected(monkeypatch, isolated_cache):
    """A weird tag value must not produce an UpdateInfo."""
    cm = _mock_response({
        "tag_name": "javascript:alert(1)",
        "html_url": "https://github.com/x/y",
    })
    _patch_opener(monkeypatch, cm)

    info = check_for_update(repo="r", current="0.5.0", now=1_000_000)
    assert info is None


def test_off_host_release_url_stripped(monkeypatch, isolated_cache):
    """If GitHub returns a non-github.com URL, surface no link rather than a bad one."""
    cm = _mock_response({
        "tag_name": "v0.6.0",
        "html_url": "https://evil.example/owner/repo",
    })
    _patch_opener(monkeypatch, cm)

    info = check_for_update(repo="r", current="0.5.0", now=1_000_000)
    assert info is not None
    assert info.latest == "v0.6.0"
    assert info.release_url == ""


def test_request_includes_github_api_headers(monkeypatch, isolated_cache):
    """Outgoing request must carry the GitHub Accept + version headers."""
    cm = _mock_response({"tag_name": "v0.6.0", "html_url": "https://github.com/x/y"})
    opener = _patch_opener(monkeypatch, cm)

    check_for_update(repo="r", current="0.5.0", now=1_000_000)

    req = opener.open.call_args.args[0]
    # urllib.request.Request stores headers with capitalized keys.
    assert req.get_header("Accept") == "application/vnd.github+json"
    assert req.get_header("X-github-api-version".replace("-", "-")) == "2022-11-28" \
        or req.get_header("X-Github-Api-Version") == "2022-11-28"
    assert "EfficientAssetRipper" in (req.get_header("User-agent") or "")
