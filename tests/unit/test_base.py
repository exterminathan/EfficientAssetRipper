"""Tests for `_base.base_dir()` — covers source vs. PyInstaller frozen modes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import _base

pytestmark = pytest.mark.unit


def test_base_dir_returns_repo_in_source_mode(monkeypatch):
    """When sys.frozen is unset, base_dir() points at the directory of _base.py."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    result = _base.base_dir()
    assert result.is_dir()
    # _base.py lives at repo root, so the result should contain it
    assert (result / "_base.py").is_file()


def test_base_dir_returns_exe_dir_when_frozen(monkeypatch, tmp_path):
    """When PyInstaller sets sys.frozen=True, base_dir() returns sys.executable's parent."""
    fake_exe_dir = tmp_path / "dist_simulated"
    fake_exe_dir.mkdir()
    fake_exe = fake_exe_dir / "EfficientAssetRipper.exe"
    fake_exe.write_bytes(b"\x4d\x5a")  # MZ header (any 2 bytes work)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    result = _base.base_dir()
    assert result == fake_exe_dir.resolve()
