"""Persistent queue checkpoint for resuming interrupted batches.

Why
---

A batch of 500 assets that loses power on asset 412 should not start
over from zero. The :class:`JobManager` rewrites this checkpoint after
every completed job, so a crash loses at most the in-flight asset.

File format (``cache/queue_checkpoint.json``)
---------------------------------------------

``{
    "version": 1,
    "saved_at": "<ISO 8601 UTC>",
    "active_profile": "<name>",
    "pending": [<AssetEntry dict>, ...],
    "completed": ["<psk_path>", ...]
}``

The checkpoint deliberately does NOT carry the Blender exe / output
dir / timeout. Those come from the caller's current config at resume
time so a config change between runs takes effect.

Atomicity
---------

:func:`save` writes to ``<path>.tmp`` first then ``os.replace``\\s, so
an interrupted write never leaves a half-baked file on disk.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from _base import base_dir
from core.asset_scanner import AssetEntry, _asset_to_dict, _dict_to_asset

log = logging.getLogger(__name__)

_VERSION = 1
_DEFAULT_PATH = base_dir() / "cache" / "queue_checkpoint.json"

# Module-level so tests can monkeypatch via ``setattr(queue_checkpoint, "_DEFAULT_PATH", tmp)``
# instead of patching every entry point individually.


@dataclass
class CheckpointPayload:
    profile: str
    saved_at: str
    pending: list[AssetEntry]
    completed: list[str]

    @property
    def remaining(self) -> list[AssetEntry]:
        """``pending`` minus anything already in ``completed``.

        Returned in the same order as ``pending`` so a resumed batch
        replays the original sequence.
        """
        done = set(self.completed)
        return [a for a in self.pending if str(a.psk_path) not in done]


def path() -> Path:
    """Return the canonical checkpoint path. Overridable in tests."""
    return _DEFAULT_PATH


def exists() -> bool:
    return path().is_file()


def save(*, profile: str, pending: list[AssetEntry], completed: list[str]) -> Path:
    """Write the checkpoint atomically. Returns the destination path."""
    dest = path()
    dest.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": _VERSION,
        "saved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "active_profile": profile,
        "pending": [_asset_to_dict(a) for a in pending],
        "completed": list(completed),
    }

    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, dest)
    except OSError:
        log.exception("Failed to write queue checkpoint to %s", dest)
        # If the rename failed but the .tmp exists, sweep it so we don't leak.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    return dest


def load() -> Optional[CheckpointPayload]:
    """Read and parse the checkpoint, or None if missing / wrong version."""
    p = path()
    if not p.is_file():
        return None
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        log.error("Queue checkpoint at %s is unreadable: %s", p, e)
        return None

    if not isinstance(data, dict) or data.get("version") != _VERSION:
        log.warning(
            "Queue checkpoint version mismatch (got %r, want %d) — ignoring",
            data.get("version") if isinstance(data, dict) else None,
            _VERSION,
        )
        return None

    pending_raw = data.get("pending") or []
    if not isinstance(pending_raw, list):
        log.error("Queue checkpoint 'pending' is not a list; ignoring")
        return None

    try:
        pending = [_dict_to_asset(d) for d in pending_raw]
    except (KeyError, TypeError) as e:
        log.error("Queue checkpoint 'pending' contains malformed entries: %s", e)
        return None

    completed_raw = data.get("completed") or []
    completed = [c for c in completed_raw if isinstance(c, str)]

    return CheckpointPayload(
        profile=str(data.get("active_profile") or ""),
        saved_at=str(data.get("saved_at") or ""),
        pending=pending,
        completed=completed,
    )


def delete() -> None:
    """Remove the checkpoint. No-op if it doesn't exist."""
    p = path()
    try:
        p.unlink(missing_ok=True)
    except OSError:
        log.exception("Failed to delete queue checkpoint %s", p)
