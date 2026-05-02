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

Hardening:
- Body is capped at 64 KB; oversized Content-Length aborts the read.
- ``tag_name`` must match a strict semver-ish pattern before being trusted.
- Redirects are rejected unless they stay on ``api.github.com``.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from _base import base_dir
from _version import __version__

log = logging.getLogger(__name__)

_DEFAULT_REPO = "exterminathan/EfficientAssetRipper"
_API_HOST = "api.github.com"
_API_URL_FMT = f"https://{_API_HOST}/repos/{{repo}}/releases/latest"
_CACHE_TTL_SECONDS = 24 * 60 * 60

# Hardening limits
_MAX_BODY_BYTES = 64 * 1024          # 64 KB read cap
_MAX_DECLARED_BYTES = 256 * 1024     # 256 KB Content-Length ceiling
_MAX_TAG_LEN = 64
_TAG_RE = re.compile(r"v?\d+(\.\d+){0,4}(-[\w.]+)?")
_RELEASE_HOST = "github.com"


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    is_newer: bool
    release_url: str


class _SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject any redirect that leaves :data:`_API_HOST`.

    GitHub's release endpoint should never 30x off-host. A redirect to a
    foreign domain is either an attack-in-the-middle or a misconfiguration —
    either way, refuse to follow.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        host = urllib.parse.urlparse(newurl).hostname or ""
        if host.lower() != _API_HOST:
            raise urllib.error.HTTPError(
                newurl, code,
                f"refusing redirect off {_API_HOST}: {host}",
                headers, fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _semver_tuple(s: str) -> tuple[int, ...]:
    """Loose semver parser: '0.5.1', 'v0.5.1', '0.5.1-rc1' all work."""
    s = s.lstrip("vV")
    base = s.split("-", 1)[0]
    parts = re.findall(r"\d+", base)
    return tuple(int(p) for p in parts) or (0,)


def is_newer(latest: str, current: str) -> bool:
    """Strict greater-than on the numeric components."""
    return _semver_tuple(latest) > _semver_tuple(current)


def _is_valid_tag(tag: str) -> bool:
    """Reject tag_name values that aren't a plausible version string."""
    if not isinstance(tag, str):
        return False
    if not tag or len(tag) >= _MAX_TAG_LEN:
        return False
    return _TAG_RE.fullmatch(tag) is not None


def _is_safe_release_url(url: str) -> bool:
    """A release URL is safe iff it's https://github.com/…."""
    if not isinstance(url, str) or not url:
        return False
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host == _RELEASE_HOST


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


def _build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_SameHostRedirectHandler())


def _fetch_latest_release(repo: str, timeout: float = 5.0) -> Optional[dict]:
    url = _API_URL_FMT.format(repo=repo)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"EfficientAssetRipper/{__version__}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    opener = _build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            # Refuse to even start the read if Content-Length looks abusive.
            length = resp.headers.get("Content-Length")
            if length is not None:
                try:
                    if int(length) > _MAX_DECLARED_BYTES:
                        log.debug(
                            "update_check: rejecting oversized response (Content-Length=%s)",
                            length,
                        )
                        return None
                except ValueError:
                    pass
            raw = resp.read(_MAX_BODY_BYTES)
        return json.loads(raw)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError, ValueError) as e:
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
    if not _is_valid_tag(latest):
        log.debug("update_check: rejecting suspicious tag_name: %r", latest)
        return None
    # An unsafe URL doesn't invalidate the version comparison, but we won't
    # surface a clickable link to anywhere off github.com.
    safe_url = url if _is_safe_release_url(url) else ""
    return UpdateInfo(
        current=current,
        latest=latest,
        is_newer=is_newer(latest, current),
        release_url=safe_url,
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

    def shutdown(self, timeout_ms: int = 2000) -> None:
        """Block until the background worker exits (or *timeout_ms* elapses)."""
        worker = self._worker
        if worker is None:
            return
        if worker.isRunning():
            worker.quit()
            worker.wait(timeout_ms)

    def _on_finished(self, info: Optional[UpdateInfo]) -> None:
        self.check_complete.emit(info)
        if info and info.is_newer:
            self.update_available.emit(info)
