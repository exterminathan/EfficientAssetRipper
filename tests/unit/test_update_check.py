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
# check_for_update — patched urlopen
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_cache(monkeypatch, tmp_path):
    """Redirect the cache file to tmp_path."""
    cache_file = tmp_path / "update_check.json"
    monkeypatch.setattr(update_check, "_cache_path", lambda: cache_file)
    return cache_file


def _mock_urlopen_response(payload: dict) -> MagicMock:
    """Build a context-manager mock returning JSON bytes."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = None
    return cm


def test_check_for_update_returns_info_when_newer(monkeypatch, isolated_cache):
    cm = _mock_urlopen_response({
        "tag_name": "v0.6.0",
        "html_url": "https://example.com/release/0.6.0",
    })
    monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda *a, **k: cm)

    info = check_for_update(repo="some/repo", current="0.5.0", now=1_000_000)
    assert isinstance(info, UpdateInfo)
    assert info.latest == "v0.6.0"
    assert info.current == "0.5.0"
    assert info.is_newer is True
    assert info.release_url == "https://example.com/release/0.6.0"


def test_check_for_update_returns_info_when_equal(monkeypatch, isolated_cache):
    cm = _mock_urlopen_response({"tag_name": "v0.5.0", "html_url": ""})
    monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda *a, **k: cm)

    info = check_for_update(repo="some/repo", current="0.5.0", now=1_000_000)
    assert info is not None
    assert info.is_newer is False


def test_check_for_update_silent_on_network_error(monkeypatch, isolated_cache):
    def _raise(*a, **k):
        raise urllib.error.URLError("nope")

    monkeypatch.setattr(update_check.urllib.request, "urlopen", _raise)

    # Must not raise
    info = check_for_update(repo="some/repo", current="0.5.0", now=1_000_000)
    assert info is None


def test_check_for_update_silent_on_malformed_json(monkeypatch, isolated_cache):
    resp = MagicMock()
    resp.read.return_value = b"not json"
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = None
    monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda *a, **k: cm)

    info = check_for_update(repo="some/repo", current="0.5.0", now=1_000_000)
    assert info is None


def test_check_for_update_caches_response(monkeypatch, isolated_cache: Path):
    """Second call within 24h must skip the network and read from cache."""
    cm = _mock_urlopen_response({
        "tag_name": "v0.7.0",
        "html_url": "https://example.com/0.7.0",
    })
    call_counter = {"n": 0}

    def _urlopen(*a, **k):
        call_counter["n"] += 1
        return cm

    monkeypatch.setattr(update_check.urllib.request, "urlopen", _urlopen)

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
    cm1 = _mock_urlopen_response({"tag_name": "v0.7.0", "html_url": "u1"})
    cm2 = _mock_urlopen_response({"tag_name": "v0.8.0", "html_url": "u2"})
    responses = iter([cm1, cm2])
    monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda *a, **k: next(responses))

    t0 = 1_000_000
    info1 = check_for_update(repo="r", current="0.5.0", now=t0)
    assert info1 is not None and info1.latest == "v0.7.0"

    # 25h later: cache expired, must re-fetch.
    t1 = t0 + 25 * 60 * 60
    info2 = check_for_update(repo="r", current="0.5.0", now=t1)
    assert info2 is not None and info2.latest == "v0.8.0"


def test_check_for_update_cache_refreshes_when_repo_changes(monkeypatch, isolated_cache: Path):
    cm = _mock_urlopen_response({"tag_name": "v9.9.9", "html_url": "u"})
    call_counter = {"n": 0}

    def _urlopen(*a, **k):
        call_counter["n"] += 1
        return cm

    monkeypatch.setattr(update_check.urllib.request, "urlopen", _urlopen)

    check_for_update(repo="repoA", current="0.5.0", now=1_000_000)
    check_for_update(repo="repoB", current="0.5.0", now=1_000_000 + 1)
    assert call_counter["n"] == 2, "different repo should bypass cache"


def test_cache_file_corrupt_falls_back_to_fetch(monkeypatch, isolated_cache: Path):
    isolated_cache.write_text("{not json", encoding="utf-8")

    cm = _mock_urlopen_response({"tag_name": "v0.6.0", "html_url": ""})
    monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda *a, **k: cm)

    info = check_for_update(repo="r", current="0.5.0", now=1_000_000)
    assert info is not None
    assert info.latest == "v0.6.0"
