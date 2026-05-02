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


def test_pick_closest_tie_breaks_deterministically():
    """When candidates score equally, sort by (len(parts), str.lower())."""
    # Same drive, same number of parts → string comparison.
    a = Path(r"D:\Other\X.tga")
    b = Path(r"E:\Other\X.tga")
    ref = Path(r"F:\Nothing\Common.psk")
    chosen = _pick_closest_path([a, b], ref)
    # 'd:\\other\\x.tga' < 'e:\\other\\x.tga' lexicographically
    assert chosen == a
    # Reverse the input order — result must still be 'a'.
    chosen2 = _pick_closest_path([b, a], ref)
    assert chosen2 == a


def test_pick_closest_tiebreak_prefers_shorter_path():
    """Tied score → prefer the candidate with fewer path parts."""
    short = Path(r"C:\one.tga")
    deep = Path(r"C:\a\b\c\one.tga")
    ref = Path(r"D:\Nothing\Else.psk")
    chosen = _pick_closest_path([deep, short], ref)
    assert chosen == short


def test_pick_closest_with_empty_reference_picks_shortest_lex_first():
    """Empty reference: deterministic by length then string."""
    a = Path(r"C:\X\one.tga")
    b = Path(r"C:\Y\two.tga")
    chosen1 = _pick_closest_path([a, b], Path())
    chosen2 = _pick_closest_path([b, a], Path())
    assert chosen1 == chosen2 == a


def test_pick_closest_stable_under_input_shuffle():
    """Shuffling the candidate list must not change the pick."""
    cands = [
        Path(r"C:\Game\Char\Other\Tex.tga"),
        Path(r"C:\Game\Char\Trooper\Tex.tga"),
        Path(r"C:\Game\Other\Trooper\Tex.tga"),
    ]
    ref = Path(r"C:\Game\Char\Trooper\Mesh.psk")
    expected = _pick_closest_path(list(cands), ref)
    # Various shuffles
    for order in (
        [2, 0, 1],
        [1, 2, 0],
        [0, 2, 1],
    ):
        shuffled = [cands[i] for i in order]
        assert _pick_closest_path(shuffled, ref) == expected
