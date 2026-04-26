# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-04-26

Initial public beta release.

### Added
- **Asset Scanner** with [Everything SDK](https://www.voidtools.com/) for
  instant search across game directories.
- **Material Resolver** — parses `.props.txt` metadata, follows material
  inheritance chains, classifies textures by PBR slot (Base Color, Normal,
  ORM, Emissive, Roughness, Metallic, Mask).
- **Headless Blender pipeline** — imports PSK/PSKX, builds full PBR shader
  node graphs, saves `.blend` files. Reuses bundled `io_scene_psk_psa`
  extension.
- **VFS Unpacker** — built-in CUE4Parse CLI mounts `.pak`/`.utoc` archives
  with AES decryption and exports meshes, textures, animations, and audio.
- **Batch processing queue** with progress tracking, logging, and cancel.
- **Per-game profiles** for game folder, AES keys, UE version, and processed
  asset lists.
- **Texture preset system** with per-material overrides
  ([data/texture_presets.json](data/texture_presets.json)).
- **WWise audio support** — scans `AkAudioEvent` assets, exports WEM, and
  converts to WAV/OGG via vgmstream.
- **Blend Combiner** — merge multiple `.blend` files into a single grid
  scene.
- **First-run setup wizard** with auto-detection for Blender, Everything,
  and the .NET runtime.
- **Auto-update check** against the GitHub Releases API on startup
  (cached for 24 hours, fails silently when offline).
- **Themeable UI** — 4 built-in colour schemes (Dusk, Bloom, Slate,
  Midnight) plus a custom scheme editor.
- **PyInstaller `--onedir` build** producing a portable
  `EfficientAssetRipper-win-x64.zip`.
- **CI/CD** — GitHub Actions workflows for PR/main builds and tag-driven
  releases.
- **Test suite** — pytest unit / integration / Qt tiers (~8s),
  plus opt-in `requires_blender` / `requires_everything` /
  `requires_dotnet_cli` e2e markers.
- **MIT License** and legal disclaimer for asset extraction usage.

[Unreleased]: https://github.com/exterminathan/EfficientAssetRipper/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/exterminathan/EfficientAssetRipper/releases/tag/v0.5.0
