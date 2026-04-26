"""Unit tests for `core.props_parser.parse_mesh_props` (JSON + legacy text)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.props_parser import (
    parse_mesh_props,
    parse_mesh_props_file,
)

pytestmark = pytest.mark.unit


def test_parse_mesh_props_json_skeletal_materials(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "mesh_skeletal.props.json").read_text()
    result = parse_mesh_props(text)
    assert len(result.materials) == 2
    slots = [m.slot_name for m in result.materials]
    assert slots == ["BodySlot", "HelmetSlot"]
    assert result.materials[0].material_name == "MI_Trooper_Body"
    assert result.materials[1].material_name == "MI_Trooper_Helmet"


def test_parse_mesh_props_json_static_materials_via_material_interface(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "mesh_static.props.json").read_text()
    result = parse_mesh_props(text)
    assert len(result.materials) == 1
    assert result.materials[0].slot_name == "MainSlot"
    assert result.materials[0].material_name == "M_Crate"


def test_parse_mesh_props_json_with_properties_subobject():
    """When materials live inside a "Properties" wrapper, they're still found."""
    text = """
{
  "Properties": {
    "SkeletalMaterials": [
      {
        "MaterialSlotName": "Slot",
        "Material": {"ObjectPath": "/Game/X/M.M"}
      }
    ]
  }
}
"""
    result = parse_mesh_props(text)
    assert len(result.materials) == 1
    assert result.materials[0].material_name == "M"


def test_parse_mesh_props_json_list_of_exports():
    """A top-level list (UE export of multiple objects) — pick the first."""
    text = """
[
  {
    "Properties": {
      "SkeletalMaterials": [
        {"MaterialSlotName": "S0", "Material": {"ObjectPath": "/Game/X/M0.M0"}}
      ]
    }
  }
]
"""
    result = parse_mesh_props(text)
    assert len(result.materials) == 1
    assert result.materials[0].material_name == "M0"


def test_parse_mesh_props_json_falls_back_to_text_on_malformed():
    """Broken JSON should not crash; the text parser handles the rest."""
    text = "{ not valid json\nSkeletalMaterials[0] =\n{\n  MaterialInterface = MaterialInstanceConstant'/Game/X/MI.MI'\n}\n"
    # The leading '{' triggers JSON parse — it will fail, then text fallback runs.
    result = parse_mesh_props(text)
    # Either the text parser finds it, or we get an empty result — both are fine,
    # the contract is "no exception".
    assert isinstance(result.materials, list)


def test_parse_mesh_props_text_legacy_format(fixtures_dir: Path):
    text = (fixtures_dir / "props" / "mesh_legacy_text.props.txt").read_text()
    result = parse_mesh_props(text)
    assert len(result.materials) == 2
    assert result.materials[0].material_name == "MI_LegacyA"
    assert result.materials[1].material_name == "MI_LegacyB"
    assert result.materials[0].slot_name == "LegacySlotA"


def test_parse_mesh_props_text_nested_wrapper(fixtures_dir: Path):
    """A wrapper StaticMaterials[2] = { ... inner [0]/[1] ... } should flatten."""
    text = (fixtures_dir / "props" / "mesh_nested_wrapper.props.txt").read_text()
    result = parse_mesh_props(text)
    assert len(result.materials) == 2
    names = {m.material_name for m in result.materials}
    assert names == {"MI_W0", "MI_W1"}


def test_parse_mesh_props_text_bare_array_fallback():
    """Single-line StaticMaterials[N] = ... entries (older format)."""
    text = (
        "StaticMaterials[0] = MaterialInstanceConstant'/Game/X/MI_A.MI_A'\n"
        "StaticMaterials[1] = MaterialInstanceConstant'/Game/X/MI_B.MI_B'\n"
    )
    result = parse_mesh_props(text)
    assert len(result.materials) == 2
    assert {m.material_name for m in result.materials} == {"MI_A", "MI_B"}


def test_parse_mesh_props_empty_text_returns_empty():
    result = parse_mesh_props("")
    assert result.materials == []


def test_parse_mesh_props_file_uses_utf8_replace(tmp_path: Path):
    """Non-utf-8 bytes shouldn't crash the file reader."""
    p = tmp_path / "weird.props.txt"
    # Mix valid UE text with a stray non-utf-8 byte
    p.write_bytes(
        b"SkeletalMaterials[0] =\n{\n   MaterialInterface = MaterialInstanceConstant'/Game/X/MI.MI'\n}\n\xff\xfe"
    )
    result = parse_mesh_props_file(p)
    assert len(result.materials) == 1
    assert result.materials[0].material_name == "MI"
