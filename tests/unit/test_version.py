"""Tests for the single source of truth `_version.__version__`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[a-z0-9.]+)?$")


def test_version_importable():
    from _version import __version__
    assert isinstance(__version__, str) and __version__


def test_version_matches_semver():
    from _version import __version__
    assert _SEMVER.match(__version__), (
        f"__version__ {__version__!r} is not semver-shaped (X.Y.Z[-prerelease])"
    )


def test_version_matches_changelog_top_entry():
    """The first `## [x.y.z]` block in CHANGELOG.md must match __version__.

    This catches the common slip-up of bumping one but not the other.
    """
    from _version import __version__

    repo_root = Path(__file__).resolve().parent.parent.parent
    changelog = repo_root / "CHANGELOG.md"
    assert changelog.is_file(), "CHANGELOG.md missing at repo root"

    text = changelog.read_text(encoding="utf-8")

    # Skip the [Unreleased] header; find the first concrete version block.
    match = re.search(r"^##\s+\[(\d+\.\d+\.\d+(?:-[a-z0-9.]+)?)\]", text, re.MULTILINE)
    assert match, "CHANGELOG.md has no `## [x.y.z]` version block"
    top_version = match.group(1)

    assert top_version == __version__, (
        f"CHANGELOG.md top entry is {top_version!r} but __version__ is "
        f"{__version__!r} — bump them together."
    )
