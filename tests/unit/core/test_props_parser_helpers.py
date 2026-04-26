"""Unit tests for `core.props_parser` helper functions and regexes."""

from __future__ import annotations

import pytest

from core.props_parser import (
    _ASSET_RE,
    _PARENT_RE,
    _extract_asset_name,
    _split_material_blocks,
)

pytestmark = pytest.mark.unit


def test_extract_asset_name_simple():
    assert _extract_asset_name("/Game/Foo/M_Body.M_Body") == "M_Body"


def test_extract_asset_name_subasset():
    assert _extract_asset_name("/Game/Foo/Package.SubAsset") == "SubAsset"


def test_extract_asset_name_numeric_export():
    """Numeric suffix is an export index — fall back to the package name."""
    assert _extract_asset_name("/Game/Foo/MI_Beetle.0") == "MI_Beetle"


def test_extract_asset_name_no_dot():
    assert _extract_asset_name("/Game/Foo/Bare") == "Bare"


def test_extract_asset_name_empty_string():
    assert _extract_asset_name("") == ""


def test_split_material_blocks_handles_depth():
    """Inner braces should not prematurely close the outer block."""
    text = """
SkeletalMaterials[0] =
{
   MaterialSlotName = SlotA
   ImportedMaterialSlotName = ( Inner = { nested = 1 } )
   MaterialInterface = MaterialInstanceConstant'/Game/X/MI_A.MI_A'
}
SkeletalMaterials[1] =
{
   MaterialSlotName = SlotB
   MaterialInterface = MaterialInstanceConstant'/Game/X/MI_B.MI_B'
}
"""
    blocks = _split_material_blocks(text)
    assert len(blocks) == 2
    assert "SlotA" in blocks[0]
    assert "SlotB" in blocks[1]


def test_split_material_blocks_returns_empty_for_no_match():
    assert _split_material_blocks("nothing\nhere") == []


def test_asset_re_matches_material_instance_constant():
    line = "MaterialInterface = MaterialInstanceConstant'/Game/X/MI_X.MI_X'"
    m = _ASSET_RE.search(line)
    assert m is not None
    assert m.group(1) == "/Game/X/MI_X.MI_X"


def test_asset_re_matches_texture2d():
    line = "ParameterValue = Texture2D'/Game/X/T_Y.T_Y'"
    m = _ASSET_RE.search(line)
    assert m is not None and m.group(1) == "/Game/X/T_Y.T_Y"


def test_parent_re_matches_short_form():
    """Parent ObjectName form: MaterialInstanceConstant'MI_Factory'"""
    m = _PARENT_RE.search("Parent = MaterialInstanceConstant'MI_Factory_2D'")
    assert m is not None
    assert m.group(1) == "MI_Factory_2D"
