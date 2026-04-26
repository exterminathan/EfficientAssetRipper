"""GitHub Releases auto-update check.

Fetches the latest published release from the GitHub API, compares the
tag against ``_version.__version__``, and emits a Qt signal with the
result. Designed to fail silently — a missing network, rate-limited API,
or malformed response should never bubble an error up to the user.

Behaviour:
- Cache the response in ``cache/update_check.json`` for 24 hours so we
  don't hammer the API on every startup.
- Run on a background QThread so we don't delay the splash.
- Default repo is ``exterminathan/EfficientAssetRipper``.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from _base import base_dir
from _version import __version__

log = logging.getLogger(__name__)

_DEFAULT_REPO = "exterminathan/EfficientAssetRipper"
_API_URL_FMT = "https://api.github.com/repos/{repo}/releases/latest"
_CACHE_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    is_newer: bool
    release_url: str


def _semver_tuple(s: str) -> tuple[int, ...]:
    """Loose semver parser: '0.5.1', 'v0.5.1', '0.5.1-rc1' all work."""
    s = s.lstrip("vV")
    base = s.split("-", 1)[0]
    parts = re.findall(r"\d+", base)
    return tuple(int(p) for p in parts) or (0,)


def is_newer(latest: str, current: str) -> bool:
    """Strict greater-than on the numeric components."""
    return _semver_tuple(latest) > _semver_tuple(current)


def _cache_path() -> Path:
    return base_dir() / "cache" / "update_check.json"


def _read_cache(now: float) -> Optional[dict]:
    path = _cache_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    fetched_at = data.get("fetched_at", 0)
    if not isinstance(fetched_at, (int, float)):
        return None
    if now - fetched_at > _CACHE_TTL_SECONDS:
        return None
    return data


def _write_cache(payload: dict, now: float) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = dict(payload)
        body["fetched_at"] = now
        path.write_text(json.dumps(body), encoding="utf-8")
    except OSError as e:
        log.debug("update_check: cache write failed: %s", e)


def _fetch_latest_release(repo: str, timeout: float = 5.0) -> Optional[dict]:
    url = _API_URL_FMT.format(repo=repo)
    req = urllib.request.Request(
        url, headers={"User-Agent": f"EfficientAssetRipper/{__version__}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        return json.loads(raw)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
        log.debug("update_check: fetch failed: %s", e)
        return None


def check_for_update(
    repo: str = _DEFAULT_REPO,
    current: str = __version__,
    *,
    now: Optional[float] = None,
) -> Optional[UpdateInfo]:
    """Return UpdateInfo (or None on any failure). Honours the 24h cache."""
    now = now if now is not None else time.time()
    cached = _read_cache(now)
    if cached and cached.get("repo") == repo:
        latest = str(cached.get("tag_name", ""))
        url = str(cached.get("html_url", ""))
    else:
        payload = _fetch_latest_release(repo)
        if not payload:
            return None
        latest = str(payload.get("tag_name", ""))
        url = str(payload.get("html_url", ""))
        _write_cache({"tag_name": latest, "html_url": url, "repo": repo}, now)

    if not latest:
        return None
    return UpdateInfo(
        current=current,
        latest=latest,
        is_newer=is_newer(latest, current),
        release_url=url,
    )


# ---------------------------------------------------------------------------
# Qt wrapper
# ---------------------------------------------------------------------------

class _UpdateCheckWorker(QThread):
    finished_with = Signal(object)  # UpdateInfo or None

    def __init__(self, repo: str, parent=None):
        super().__init__(parent)
        self._repo = repo

    def run(self):
        try:
            self.finished_with.emit(check_for_update(self._repo))
        except Exception as e:  # last-ditch: must never crash the app
            log.debug("update_check: worker exception: %s", e)
            self.finished_with.emit(None)


class UpdateChecker(QObject):
    """Run a single update check in the background. Reusable per app run."""

    update_available = Signal(object)   # UpdateInfo (only when newer)
    check_complete = Signal(object)     # UpdateInfo or None (always fires)

    def __init__(self, repo: str = _DEFAULT_REPO, parent=None):
        super().__init__(parent)
        self._repo = repo
        self._worker: Optional[_UpdateCheckWorker] = None

    def start(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = _UpdateCheckWorker(self._repo, self)
        self._worker.finished_with.connect(self._on_finished)
        self._worker.start()

    def _on_finished(self, info: Optional[UpdateInfo]) -> None:
        self.check_complete.emit(info)
        if info and info.is_newer:
            self.update_available.emit(info)
