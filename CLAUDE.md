# CLAUDE.md — EfficientAssetRipper

Reference notes for future sessions. The README is comprehensive — read it first for user-facing detail. This file captures the architectural shape that isn't obvious from a single file read.

## What this is

A PySide6 desktop app (Windows-first) that turns Unreal Engine 5 game files into ready-to-use Blender `.blend` scenes. Pipeline: **Unpack** (`.pak`/`.utoc` → loose files) → **Scan** (find PSK/PSKX) → **Resolve** (parse `.props.txt` for materials/textures) → **Process** (headless Blender wires PBR shader graphs and saves `.blend`).

Two big subsystems live in one repo:
- **Python GUI + orchestration** (PySide6 / Qt6).
- **CUE4ParseCLI** — a separate .NET 8.0 project that does all UE archive parsing/extraction, talking to the GUI over NDJSON on stdio.

## Layout

| Path | Role |
|------|------|
| [main.py](main.py) | App entry. Builds icon, applies theme, runs splash, shows [gui/main_window.py](gui/main_window.py). |
| [_base.py](_base.py) | `base_dir()` — resolves project root for both source and frozen PyInstaller exe. **Use this everywhere — never hardcode paths.** |
| [config.py](config.py) | Thin wrapper over `QSettings` (registry: `HKCU\Software\EfficientAssetRipper`). Defaults defined in `_DEFAULTS`. Per-game data lives in `profiles/`, not here. |
| [core/](core/) | Headless backend logic. No Qt widgets, but Qt signals/threads are OK. |
| [gui/](gui/) | All PySide6 UI. Imports from `core/`, never the reverse. |
| [blender/](blender/) | Scripts run **inside** Blender as a subprocess (`blender --background --python ...`). They `import bpy` — do NOT import from `core/` or `gui/`. |
| [cue4parse_cli/](cue4parse_cli/) | .NET 8.0 single-file CLI, embeds CUE4Parse + Newtonsoft.Json. The Python side never reads `.pak` directly. |
| [data/texture_presets.json](data/texture_presets.json) | Texture-suffix → PBR-slot rules and per-material overrides. Single source of truth for wiring decisions. |
| [profiles/](profiles/) | Per-game JSON (gitignored). Has `game_dir`, `aes_keys`, `ue_version`, `psk_processed`, etc. |
| [cache/](cache/), [outputs/](outputs/), [logs/](logs/) | Gitignored runtime data. `cache/scan_<md5>.json` keyed by game folder hash. |
| [build.bat](build.bat) | PyInstaller `--onedir` build that also dotnet-publishes the CLI and zips everything. |
| [sync.ps1](sync.ps1) | Robocopy of gitignored `profiles/`/`cache/` to a Drive backup. Don't auto-run. |

## Architectural rules

1. **`core/` ↔ `gui/`**: `gui` imports `core`, never the reverse. Long work in `core/` runs in a `QThread` (`ScanWorker`, `RescanWorker`, `JobManager`) and reports back via Qt signals.
2. **All paths go through `base_dir()`** from [_base.py](_base.py:7). PyInstaller frozen exes put data files next to the .exe; source runs use the repo root. Hardcoded absolute paths break the build.
3. **Blender scripts are isolated.** [blender/process_asset.py](blender/process_asset.py) and [blender/material_setup.py](blender/material_setup.py) only see `bpy` + a JSON manifest. They communicate **back** by printing `##ASSET_STATUS##{...json...}` lines, parsed in [core/blender_runner.py:100](core/blender_runner.py#L100).
4. **CUE4ParseCLI IPC is NDJSON over stdio.** [core/unpacker.py](core/unpacker.py) wraps it as a `QObject` with signals. Every line is one JSON object. Commands: `init`, `browse`, `export`, `export_folder`, `cancel`, `get_props`, `scan_wwise_events`, `list_exports`, `export_wwise_audio`, `quit`. Reply types: `init_done`, `browse_result`, `progress`, `export_done`, `props_result`, `exports_listed`, `wwise_scan_result`, `warning`, `error`, `cancelled`, `quit_ack`.
5. **Asset resolution is suffix-driven, not path-driven.** [core/texture_resolver.py:64](core/texture_resolver.py#L64) classifies textures by suffix (`_C`, `_N`, `_ORM`, …) first, then `param_names` from props as fallback. Material overrides in `texture_presets.json` can force specific textures into specific slots.
6. **Material parent chains are followed.** A material with no textures of its own walks `parent_name` upward via [core/asset_scanner.py:_resolve_parent_chain](core/asset_scanner.py#L394), merging color tints / scalar params child-overrides-parent.
7. **Scan results are cached per game folder.** Cache file is `cache/scan_<md5(game_folder)>.json`, version-tagged (`_CACHE_VERSION`). On scan, already-cached PSK paths with `mesh_props_found=True` are reused.
8. **Profile vs global settings.** Keys in `core/profile_manager.PROFILE_KEYS` belong to the active profile JSON; everything else lives in QSettings. Switching profiles re-reads the per-game state. `migrate_from_qsettings` runs on first launch to seed a `Default` profile.

## CUE4ParseCLI rebuild

The repo lived on Google Drive at one point; .NET file locks broke `dotnet publish` in-place. Build into temp first, then copy back:

```powershell
dotnet publish cue4parse_cli/CUE4ParseCLI.csproj -c Release -r win-x64 `
  --self-contained true -p:PublishSingleFile=true `
  -o "$env:TEMP\CUE4ParseCLI_build"
# then copy $env:TEMP\CUE4ParseCLI_build\* → cue4parse_cli/bin/publish/
```

`build.bat` does this same thing (with a non-self-contained variant). After editing anything in `cue4parse_cli/`, rebuild before declaring the change done — the Python side loads the published exe at runtime, not the source.

## External dependencies (runtime, not pip)

- **Everything** desktop app **must be running** — [core/everything.py](core/everything.py) is a ctypes wrapper around `Everything64.dll`. No Everything = no scan. There is no fallback.
- **Blender 4.0+** — `bpy.ops.psk.import_file` from the bundled `io_scene_psk_psa` extension is the default importer (configurable via `psk_addon_name` setting).
- **.NET 8.0 Runtime** — to run CUE4ParseCLI.exe.
- **Oodle / vgmstream** — auto-downloaded by CUE4ParseCLI when first needed.

## Common gotchas

- **PSK material extraction fallback.** When a mesh `.props.txt` lists no materials, [core/asset_scanner.py:_extract_psk_materials](core/asset_scanner.py#L125) reads names from the binary `MATT0000` chunk. Don't remove this — some exports lack material refs in props.
- **Closest-path tiebreaker.** When Everything returns multiple matches for the same texture/material name, [_pick_closest_path](core/texture_resolver.py#L43) picks the candidate sharing the longest path prefix with the source PSK. Critical for games that ship duplicate names across folders.
- **Scene reset in Blender.** [process_asset.py](blender/process_asset.py) manually clears `bpy.data.objects/meshes/materials/images/armatures` rather than `read_factory_settings` — the latter wipes the extension system and breaks the PSK addon. Don't "simplify" this.
- **Status parsing requires the `##ASSET_STATUS##` prefix** — anything else on stdout is treated as plain Blender output.
- **AppUserModelID** is set in [main.py:127](main.py#L127) so the Windows taskbar shows the gem icon instead of a generic Python interpreter icon.
- **`creationflags=CREATE_NO_WINDOW`** in [core/blender_runner.py:79](core/blender_runner.py#L79) — without it Blender flashes a console window on Windows.

## Conventions

- Type-annotated, `from __future__ import annotations` at the top of `core/` modules.
- Dataclasses for plain records (`AssetEntry`, `MaterialEntry`, `ResolvedTexture`, `BlenderResult`, `MeshProps`, `MaterialProps`).
- Logging via `log = logging.getLogger(__name__)` — no `print` in `core/` / `gui/`. Blender scripts can print (the runner reads stdout).
- Comments are sparse and explain *why*. Match the existing style — don't add WHAT-comments.
- Color schemes: 4 built-in (`Dusk`, `Bloom`, `Slate`, `Midnight`) plus user-defined. Theme is applied centrally via [gui/theme.py](gui/theme.py); never hardcode colors in widgets — pull from `theme.current_scheme()`.

## Test suite

A tiered pytest suite lives in [tests/](tests/). Install dev deps once:

```
py -m pip install -r requirements-dev.txt
```

**Tiers and speed:**

| Command | Time | What it covers |
|---------|------|----------------|
| `py -m pytest tests/unit` | ~0.5s | Pure logic — parsers, classifier, resolver, scanner serialization, ctypes mocks |
| `py -m pytest tests/integration` | ~0.5s | Real fixtures + disk I/O — profile CRUD, scan cache, resolver with FakeEverythingSDK |
| `py -m pytest tests/qt` | ~7s | PySide6 widgets via pytest-qt — all panels, signals, StubQProcess |
| `py -m pytest` | ~8s | All three tiers above |
| `py -m pytest -m requires_blender` | opt-in | Needs `BLENDER_EXE` env var |
| `py -m pytest -m requires_dotnet_cli` | opt-in | Needs `CUE4PARSE_CLI` env var |
| `py -m pytest -m requires_everything` | opt-in | Needs `EVERYTHING_DLL` env var |

**Config:** [pyproject.toml](pyproject.toml) under `[tool.pytest.ini_options]` — markers, `pythonpath = ["."]`, `qt_api = "pyside6"`, and `[tool.coverage.run]`.

**Pre-build gate:** `build.bat` runs the fast tiers as step `[0b/5]` and aborts on any failure. Opt-in e2e markers are excluded from the gate.

**Key fixtures (all in [tests/conftest.py](tests/conftest.py)):**

| Fixture | What it provides |
|---------|-----------------|
| `FakeEverythingSDK` | Ctypes-free SDK stand-in; seed with `make_fake_sdk(file_index)` |
| `mock_qsettings` | Dict-backed QSettings stub; avoids touching the real Windows registry |
| `tmp_profiles_dir` | Monkeypatches `_PROFILES_DIR` to `tmp_path` for isolated profile CRUD |
| `mock_blender_run` | Patches `run_blender` at both import sites; returns a canned `BlenderResult` |
| `jedi_scan_dict` | Loads `cache/scan_b6df0cbbd18d.json` once per session (skips if absent) |

**What is NOT tested:**
- `gui/splash.py` — animation only, excluded from coverage
- Real Blender material wiring (`blender/*.py` runs inside `bpy`) — covered by opt-in e2e + manual QA
- Real Everything DLL calls — opt-in e2e only
- Real CUE4Parse archive extraction — would require a sample `.pak` + AES key; NDJSON wire protocol is covered by `StubQProcess` in [tests/qt/test_unpacker_signals.py](tests/qt/test_unpacker_signals.py)

**Adding a test:**
- **Unit** — pure function + fixture, no I/O, no Qt. Put it in `tests/unit/core/` mirroring the source path.
- **Integration** — needs disk or `FakeEverythingSDK`. Use `make_fake_sdk` + `tiny_presets` or `real_presets` for resolver tests.
- **Qt** — use `qtbot` from pytest-qt and `mock_qsettings`. Patch `QFileDialog` / `QMessageBox` static methods at the call site, not at `PySide6.QtWidgets`.
- **E2e** — mark with the relevant `@pytest.mark.requires_*` and read the binary path from the env var; auto-skip handles the rest.

## When making changes

- **Editing a `core/` resolver?** Run `py -m pytest tests/unit tests/integration` to verify the logic, then do a real scan against a profile to catch anything fixtures don't exercise.
- **Editing `cue4parse_cli/Program.cs`?** Rebuild (see above) and verify `Unpacker` panel still mounts archives.
- **Editing `blender/*.py`?** These run inside Blender, not the venv — no PySide6, no project imports outside the `blender/` dir. Test by processing one asset end-to-end.
- **Editing the `texture_presets.json` schema?** Update both the resolver and any per-material overrides in user profiles. Bump `version` if the change is breaking.
- **Editing `config._DEFAULTS`?** Consider whether the new key belongs in `PROFILE_KEYS` instead — game-specific settings should be per-profile.
- **Don't touch `dist/` or `*.spec`** — both are generated by `build.bat`. The spec file is gitignored.
