"""Unit tests for `core.texture_resolver._pick_closest_path` (pure)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.texture_resolver import _pick_closest_path

pytestmark = pytest.mark.unit


def test_pick_closest_single_candidate():
    only = Path(r"C:\X\one.tga")
    assert _pick_closest_path([only], Path(r"C:\Y\Z.psk")) is only


def test_pick_closest_longest_prefix_wins():
    a = Path(r"C:\Game\Characters\Trooper\Textures\T_Body_C.tga")
    b = Path(r"C:\Game\Characters\Other\Textures\T_Body_C.tga")
    ref = Path(r"C:\Game\Characters\Trooper\Meshes\SK_Trooper.psk")
    chosen = _pick_closest_path([a, b], ref)
    assert chosen == a


def test_pick_closest_case_insensitive_match():
    a = Path(r"C:\GAME\characters\trooper\T.tga")
    b = Path(r"C:\Game\Characters\Other\T.tga")
    ref = Path(r"C:\Game\Characters\Trooper\SK.psk")
    chosen = _pick_closest_path([a, b], ref)
    # Both share the GAME/Characters prefix; trooper matches deeper than other
    assert chosen == a


def test_pick_closest_tie_returns_first_seen():
    """When candidates score equally, the first one wins."""
    a = Path(r"D:\OtherDrive\X.tga")
    b = Path(r"E:\YetAnother\X.tga")
    ref = Path(r"F:\Nothing\In\Common.psk")
    # No common prefix at all => both score 0; first wins.
    assert _pick_closest_path([a, b], ref) is a


def test_pick_closest_with_empty_reference_returns_first():
    a = Path(r"C:\X\one.tga")
    b = Path(r"C:\Y\two.tga")
    assert _pick_closest_path([a, b], Path()) is a
