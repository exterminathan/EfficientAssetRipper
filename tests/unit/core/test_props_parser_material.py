"""Unit tests for `core.props_parser.parse_material_props` (JSON + legacy text)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.props_parser import parse_material_props

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# JSON format
# ---------------------------------------------------------------------------

def test_parse_material_props_json_texture_parameter_values(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "material_full.props.json").read_text()
    result = parse_material_props(text)
    names = {t.texture_name for t in result.textures}
    # ORM, BaseColor, Normal — but NOT Texture2DArray
    assert "T_Trooper_Body_C" in names
    assert "T_Trooper_Body_N" in names
    assert "T_Trooper_Body_ORM" in names


def test_parse_material_props_json_skips_texture2darray(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "material_full.props.json").read_text()
    result = parse_material_props(text)
    # The fixture includes a Texture2DArray entry that must be skipped
    names = {t.texture_name for t in result.textures}
    assert "T_Trooper_Array" not in names


def test_parse_material_props_json_vector_params_become_color_tints(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "material_full.props.json").read_text()
    result = parse_material_props(text)
    assert "RColorTint" in result.color_tints
    r, g, b, a = result.color_tints["RColorTint"]
    assert r == pytest.approx(0.646)
    assert g == pytest.approx(0.433)
    assert b == pytest.approx(0.168)
    assert a == pytest.approx(1.0)


def test_parse_material_props_json_scalar_params(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "material_full.props.json").read_text()
    result = parse_material_props(text)
    assert "RoughnessAdjust" in result.scalar_params
    assert result.scalar_params["RoughnessAdjust"] == pytest.approx(0.42)


def test_parse_material_props_json_two_sided_and_masked_flags(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "material_full.props.json").read_text()
    result = parse_material_props(text)
    assert result.is_two_sided is True
    assert result.is_masked is False


def test_parse_material_props_json_blend_mode_string(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "material_full.props.json").read_text()
    result = parse_material_props(text)
    assert result.blend_mode == "BLEND_Translucent"


def test_parse_material_props_json_parent_via_objectname(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "material_full.props.json").read_text()
    result = parse_material_props(text)
    assert result.parent_name == "MM_Master_PBR"


def test_parse_material_props_json_parent_via_objectpath_fallback():
    """When ObjectName is missing, ObjectPath should still yield a parent name."""
    text = """
{
  "Properties": {
    "Parent": {"ObjectPath": "/Game/Materials/MM_Fallback_Parent.MM_Fallback_Parent"}
  }
}
"""
    result = parse_material_props(text)
    assert result.parent_name == "MM_Fallback_Parent"


def test_parse_material_props_json_streaming_data_fallback():
    """When no TextureParameterValues exist, TextureStreamingData should fill in."""
    text = """
{
  "Properties": {
    "TextureStreamingData": [
      {"TextureName": "Bark_C"},
      {"TextureName": "Bark_N"},
      {"TextureName": "Bark_C"}
    ]
  }
}
"""
    result = parse_material_props(text)
    names = [t.texture_name for t in result.textures]
    # Dedup applies even in streaming fallback
    assert names == ["Bark_C", "Bark_N"]


# ---------------------------------------------------------------------------
# Legacy text format
# ---------------------------------------------------------------------------

def test_parse_material_props_text_legacy_with_param_info(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "material_legacy.props.txt").read_text()
    result = parse_material_props(text)
    names = [t.texture_name for t in result.textures]
    assert "T_Legacy_Body_C" in names
    assert "T_Legacy_Body_N" in names
    base = next(t for t in result.textures if t.texture_name == "T_Legacy_Body_C")
    assert base.param_name == "BaseColor"


def test_parse_material_props_text_dedup_textures(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "material_legacy.props.txt").read_text()
    result = parse_material_props(text)
    # Fixture has T_Legacy_Body_C listed twice — should appear once
    names = [t.texture_name for t in result.textures]
    assert names.count("T_Legacy_Body_C") == 1


def test_parse_material_props_text_two_sided_true_lower():
    text = "TwoSided = True\n"
    result = parse_material_props(text)
    assert result.is_two_sided is True


def test_parse_material_props_text_streaming_only_path(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "material_streaming_only.props.txt").read_text()
    result = parse_material_props(text)
    names = [t.texture_name for t in result.textures]
    assert "Bark_C" in names
    assert "Bark_N" in names
    assert names.count("Bark_C") == 1  # dedup


def test_parse_material_props_text_blend_mode_extract():
    text = "BlendMode = BLEND_Masked\n"
    result = parse_material_props(text)
    assert result.blend_mode == "BLEND_Masked"


def test_parse_material_props_text_vector_params_legacy(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "material_legacy.props.txt").read_text()
    result = parse_material_props(text)
    assert "RColorTint" in result.color_tints
    r, g, b, a = result.color_tints["RColorTint"]
    assert r == pytest.approx(0.646) and g == pytest.approx(0.433)


def test_parse_material_props_text_scalar_params_legacy(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "material_legacy.props.txt").read_text()
    result = parse_material_props(text)
    assert result.scalar_params.get("RoughnessAdjust") == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# Phase 3.2 — robust property parsers
# ---------------------------------------------------------------------------

def test_parse_material_props_text_color_block_alpha_defaults_to_one():
    """Legacy text parser must accept a color block missing A= and default 1.0."""
    text = """
VectorParameterValues[0] =
{
   ParameterInfo = { Name=NoAlphaTint }
   ParameterValue = { R=0.1, G=0.2, B=0.3 }
}
"""
    result = parse_material_props(text)
    assert "NoAlphaTint" in result.color_tints
    r, g, b, a = result.color_tints["NoAlphaTint"]
    assert (r, g, b) == pytest.approx((0.1, 0.2, 0.3))
    assert a == pytest.approx(1.0)


def test_parse_material_props_text_color_block_channels_in_any_order():
    """Color channels can appear in any order; parser must not lock to RGBA."""
    text = """
VectorParameterValues[0] =
{
   ParameterInfo = { Name=ReorderTint }
   ParameterValue = { B=0.3, A=0.5, R=0.1, G=0.2 }
}
"""
    result = parse_material_props(text)
    assert result.color_tints["ReorderTint"] == pytest.approx((0.1, 0.2, 0.3, 0.5))


def test_parse_material_props_json_color_alpha_defaults_to_one():
    """JSON parser also fills missing A=1.0."""
    text = """
{
  "Properties": {
    "VectorParameterValues": [
      {
        "ParameterInfo": {"Name": "NoAlpha"},
        "ParameterValue": {"R": 0.5, "G": 0.5, "B": 0.5}
      }
    ]
  }
}
"""
    result = parse_material_props(text)
    assert result.color_tints.get("NoAlpha") == pytest.approx((0.5, 0.5, 0.5, 1.0))


def test_parse_material_props_text_two_sided_strict_lower_compare():
    """`TwoSided = TrueColor` must not be misread as a True boolean."""
    text = "TwoSided = TrueColor\n"
    result = parse_material_props(text)
    assert result.is_two_sided is False


def test_parse_material_props_text_b_is_masked_strict_compare():
    text = "bIsMasked = false\n"
    result = parse_material_props(text)
    assert result.is_masked is False


# ---------------------------------------------------------------------------
# Phase 3.1 — JSONDecodeError specific path falls back cleanly
# ---------------------------------------------------------------------------

def test_parse_material_props_json_malformed_falls_back_to_text():
    """Malformed JSON should not raise — text parser takes over."""
    text = '{"this is not": valid json,\nTexture2D\'/Game/X/T_Y.T_Y\''
    result = parse_material_props(text)
    # Text parser should still find the Texture2D ref.
    assert any(t.texture_name == "T_Y" for t in result.textures)
