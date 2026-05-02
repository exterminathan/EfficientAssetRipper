"""Tests for `AssetScanner._resolve_parent_chain` cycle / depth / case handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.asset_scanner import AssetScanner, MAX_PARENT_DEPTH
from tests.conftest import FakeEverythingSDK

pytestmark = pytest.mark.integration


def _write_mat(tmp_path: Path, name: str, parent: str = "", tex_name: str = "") -> Path:
    """Drop a JSON material props file in tmp_path and return its path."""
    parent_block = ""
    if parent:
        parent_block = f"""
    "Parent": {{"ObjectName": "MaterialInstanceConstant'{parent}'"}},"""
    tex_block = ""
    if tex_name:
        tex_block = f"""
    "TextureParameterValues": [
      {{
        "ParameterInfo": {{"Name": "BaseColor"}},
        "ParameterValue": {{
          "ObjectPath": "/Game/X/Textures/{tex_name}.{tex_name}"
        }}
      }}
    ],"""
    p = tmp_path / f"{name}.props.txt"
    p.write_text(
        f"""{{
  "Properties": {{{parent_block}{tex_block}
    "TwoSided": false
  }}
}}""",
        encoding="utf-8",
    )
    return p


def _write_mesh(tmp_path: Path, mesh_name: str, mat_name: str) -> Path:
    p = tmp_path / f"{mesh_name}.props.txt"
    p.write_text(
        f"""{{
  "Properties": {{
    "SkeletalMaterials": [
      {{
        "MaterialSlotName": "BodySlot",
        "Material": {{"ObjectPath": "/Game/X/{mat_name}.{mat_name}"}}
      }}
    ]
  }}
}}""",
        encoding="utf-8",
    )
    return p


def _basic_psk(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 32)
    return path


def _tiny_presets() -> dict:
    return {
        "version": 1,
        "presets": {
            "default_pbr": {
                "priority_order": ["base_color"],
                "texture_slots": {
                    "base_color": {
                        "suffixes": ["_C"],
                        "param_names": ["BaseColor"],
                        "colorspace": "sRGB",
                        "wiring": {"type": "direct", "target_input": "Base Color"},
                    },
                },
            }
        },
    }


def test_parent_chain_terminates_on_self_reference(tmp_path):
    """A material whose Parent points back at itself must not infinite-loop."""
    psk = _basic_psk(tmp_path / "Char" / "SK_Loop.psk")

    mesh = _write_mesh(tmp_path, "SK_Loop", "MI_SelfLoop")
    # MI_SelfLoop's Parent is itself
    self_ref = _write_mat(tmp_path, "MI_SelfLoop", parent="MI_SelfLoop", tex_name="")

    sdk = FakeEverythingSDK(
        psk_files=[psk],
        props_files={
            "SK_Loop": [mesh],
            "MI_SelfLoop": [self_ref],
        },
    )
    scanner = AssetScanner(str(tmp_path), _tiny_presets(), sdk=sdk)
    results = scanner.scan()
    # Should not hang and should produce one entry with the self-loop material.
    assert len(results) == 1


def test_parent_chain_terminates_on_case_mixed_cycle(tmp_path):
    """Visited tracking must be case-insensitive — `MI_Foo` vs `mi_foo`."""
    psk = _basic_psk(tmp_path / "SK_Cycle.psk")
    mesh = _write_mesh(tmp_path, "SK_Cycle", "MI_Foo")
    a = _write_mat(tmp_path, "MI_Foo", parent="mi_foo")  # different case
    sdk = FakeEverythingSDK(
        psk_files=[psk],
        props_files={
            "SK_Cycle": [mesh],
            "MI_Foo": [a],   # FakeEverythingSDK lookups are case-insensitive
        },
    )
    scanner = AssetScanner(str(tmp_path), _tiny_presets(), sdk=sdk)
    results = scanner.scan()
    assert len(results) == 1


def test_parent_chain_a_to_b_to_a_cycle_terminates(tmp_path):
    psk = _basic_psk(tmp_path / "SK_AB.psk")
    mesh = _write_mesh(tmp_path, "SK_AB", "MI_A")
    a = _write_mat(tmp_path, "MI_A", parent="MI_B")
    b = _write_mat(tmp_path, "MI_B", parent="MI_A")
    sdk = FakeEverythingSDK(
        psk_files=[psk],
        props_files={
            "SK_AB": [mesh],
            "MI_A": [a],
            "MI_B": [b],
        },
    )
    scanner = AssetScanner(str(tmp_path), _tiny_presets(), sdk=sdk)
    results = scanner.scan()
    assert len(results) == 1


def test_parent_chain_caps_at_max_parent_depth(tmp_path):
    """A long chain of N=MAX_PARENT_DEPTH+5 must terminate at the depth cap."""
    psk = _basic_psk(tmp_path / "SK_Deep.psk")
    mesh = _write_mesh(tmp_path, "SK_Deep", "MI_L00")

    chain_len = MAX_PARENT_DEPTH + 5
    props_files: dict[str, list[Path]] = {"SK_Deep": [mesh]}
    for i in range(chain_len):
        name = f"MI_L{i:02d}"
        parent = f"MI_L{i + 1:02d}"
        # The very last one has no parent so the chain would terminate
        # naturally if we could walk all of it.
        if i == chain_len - 1:
            parent = ""
        path = _write_mat(tmp_path, name, parent=parent)
        props_files[name] = [path]

    sdk = FakeEverythingSDK(psk_files=[psk], props_files=props_files)
    scanner = AssetScanner(str(tmp_path), _tiny_presets(), sdk=sdk)
    # Should NOT raise / hang. Just verify it completes.
    results = scanner.scan()
    assert len(results) == 1
