"""Unit tests for `core.type_cache`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import type_cache as tc


# ---------------------------------------------------------------------------
# category_for_export_type
# ---------------------------------------------------------------------------

def test_category_for_export_type_known_buckets():
    assert tc.category_for_export_type("SkeletalMesh") == tc.CATEGORY_MESH
    assert tc.category_for_export_type("StaticMesh") == tc.CATEGORY_MESH
    assert tc.category_for_export_type("Texture2D") == tc.CATEGORY_TEXTURE
    assert tc.category_for_export_type("TextureCube") == tc.CATEGORY_TEXTURE
    assert tc.category_for_export_type("SoundWave") == tc.CATEGORY_AUDIO
    assert tc.category_for_export_type("AkAudioEvent") == tc.CATEGORY_AUDIO
    assert tc.category_for_export_type("Material") == tc.CATEGORY_MATERIAL
    assert tc.category_for_export_type("MaterialInstanceConstant") == tc.CATEGORY_MATERIAL
    assert tc.category_for_export_type("AnimSequence") == tc.CATEGORY_ANIMATION
    assert tc.category_for_export_type("AnimMontage") == tc.CATEGORY_ANIMATION


def test_category_for_export_type_unknown_falls_to_other():
    assert tc.category_for_export_type("ParticleSystem") == tc.CATEGORY_OTHER
    assert tc.category_for_export_type("") == tc.CATEGORY_OTHER
    assert tc.category_for_export_type("Blueprint") == tc.CATEGORY_OTHER


def test_all_categories_includes_other():
    assert tc.CATEGORY_OTHER in tc.ALL_CATEGORIES
    assert len(tc.ALL_CATEGORIES) == 6


# ---------------------------------------------------------------------------
# compute_fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_stable_across_trailing_slash_and_case():
    fp1 = tc.compute_fingerprint("/Some/Game/Path/", "GAME_UE5_4")
    fp2 = tc.compute_fingerprint("/some/game/path", "GAME_UE5_4")
    fp3 = tc.compute_fingerprint("/some/game/path\\", "GAME_UE5_4")
    assert fp1 == fp2 == fp3


def test_fingerprint_changes_when_ue_version_differs():
    fp_a = tc.compute_fingerprint("/G", "GAME_UE5_4")
    fp_b = tc.compute_fingerprint("/G", "GAME_UE5_5")
    assert fp_a != fp_b


# ---------------------------------------------------------------------------
# TypeCache add_batch / lookups
# ---------------------------------------------------------------------------

def _make_batch():
    return [
        {
            "path": "/Game/Tex.uasset",
            "exports": [{"name": "T_Foo", "export_type": "Texture2D"}],
        },
        {
            "path": "/Game/Mesh.uasset",
            "exports": [
                {"name": "SK_Hero", "export_type": "SkeletalMesh"},
                {"name": "SK_Hero_Skeleton", "export_type": "Skeleton"},
            ],
        },
    ]


def test_add_batch_populates_entries():
    cache = tc.TypeCache()
    cache.add_batch(_make_batch())
    assert set(cache.entries.keys()) == {"/Game/Tex.uasset", "/Game/Mesh.uasset"}
    assert cache.entries["/Game/Tex.uasset"] == [
        {"name": "T_Foo", "export_type": "Texture2D"},
    ]


def test_add_batch_skips_entries_without_path():
    cache = tc.TypeCache()
    cache.add_batch([{"path": "", "exports": []}, {"exports": []}, {"path": "/A.uasset", "exports": []}])
    assert list(cache.entries.keys()) == ["/A.uasset"]


def test_export_types_for_package():
    cache = tc.TypeCache()
    cache.add_batch(_make_batch())
    assert cache.export_types_for_package("/Game/Mesh.uasset") == {"SkeletalMesh", "Skeleton"}
    assert cache.export_types_for_package("/Game/Missing.uasset") == set()


def test_categories_for_package_groups_correctly():
    cache = tc.TypeCache()
    cache.add_batch(_make_batch())
    assert cache.categories_for_package("/Game/Tex.uasset") == {tc.CATEGORY_TEXTURE}
    # SkeletalMesh + Skeleton → mesh + other
    cats = cache.categories_for_package("/Game/Mesh.uasset")
    assert tc.CATEGORY_MESH in cats
    assert tc.CATEGORY_OTHER in cats


def test_categories_for_unknown_package_is_empty_set():
    cache = tc.TypeCache()
    assert cache.categories_for_package("/Game/Nope.uasset") == set()


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

def test_save_and_load_roundtrip(tmp_path: Path):
    cache = tc.TypeCache()
    cache.add_batch(_make_batch())
    cache.error_count = 2
    cache.total_packages = 10

    fp = "abc123"
    saved = cache.save(fp, cache_dir=tmp_path)
    assert saved.is_file()
    assert saved.name == "types_abc123.json"

    loaded = tc.TypeCache.load(fp, cache_dir=tmp_path)
    assert loaded is not None
    assert loaded.entries == cache.entries
    assert loaded.error_count == 2
    assert loaded.total_packages == 10


def test_load_returns_none_on_missing_file(tmp_path: Path):
    assert tc.TypeCache.load("nope", cache_dir=tmp_path) is None


def test_load_returns_none_on_version_mismatch(tmp_path: Path):
    fp = "vmismatch"
    path = tc.cache_path_for(fp, cache_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 999,
        "entries": {"/G/A.uasset": []},
    }))
    assert tc.TypeCache.load(fp, cache_dir=tmp_path) is None


def test_load_returns_none_on_corrupt_json(tmp_path: Path):
    fp = "corrupt"
    path = tc.cache_path_for(fp, cache_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert tc.TypeCache.load(fp, cache_dir=tmp_path) is None


def test_clear_resets_all_fields():
    cache = tc.TypeCache()
    cache.add_batch(_make_batch())
    cache.error_count = 5
    cache.total_packages = 100
    cache.clear()
    assert cache.entries == {}
    assert cache.error_count == 0
    assert cache.total_packages == 0


# ---------------------------------------------------------------------------
# rebuild_folder_index / categories_under_folder
# ---------------------------------------------------------------------------

def test_rebuild_folder_index_propagates_to_ancestors():
    cache = tc.TypeCache()
    cache.add_batch([
        {"path": "Game/Characters/Hair/SK_Hair.uasset",
         "exports": [{"name": "SK_Hair", "export_type": "SkeletalMesh"}]},
        {"path": "Game/Textures/T_Skin.uasset",
         "exports": [{"name": "T_Skin", "export_type": "Texture2D"}]},
    ])
    cache.rebuild_folder_index()

    # Direct parent
    assert tc.CATEGORY_MESH in cache.categories_under_folder("Game/Characters/Hair")
    # Grandparent accumulates both
    assert tc.CATEGORY_MESH in cache.categories_under_folder("Game/Characters")
    assert tc.CATEGORY_TEXTURE in cache.categories_under_folder("Game/Textures")
    # Root accumulates all
    root = cache.categories_under_folder("Game")
    assert tc.CATEGORY_MESH in root
    assert tc.CATEGORY_TEXTURE in root


def test_categories_under_folder_unknown_returns_empty():
    cache = tc.TypeCache()
    cache.rebuild_folder_index()
    assert cache.categories_under_folder("Game/NotIndexed") == frozenset()


def test_rebuild_folder_index_called_on_load(tmp_path: Path):
    cache = tc.TypeCache()
    cache.add_batch([
        {"path": "Game/Meshes/SK.uasset",
         "exports": [{"name": "SK", "export_type": "SkeletalMesh"}]},
    ])
    fp = "folderidx"
    cache.save(fp, cache_dir=tmp_path)

    loaded = tc.TypeCache.load(fp, cache_dir=tmp_path)
    assert loaded is not None
    assert tc.CATEGORY_MESH in loaded.categories_under_folder("Game/Meshes")
    assert tc.CATEGORY_MESH in loaded.categories_under_folder("Game")
