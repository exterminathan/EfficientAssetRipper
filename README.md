# EfficientAssetRipper

A desktop application for extracting Unreal Engine game assets and automatically assembling them in Blender with full material/texture wiring.

**Scan → Resolve → Process → Done.** Point it at a game folder, and EfficientAssetRipper finds meshes, resolves their materials and textures, then batch-processes everything in Blender — importing PSK/PSKX meshes, wiring PBR shader nodes, and saving ready-to-use `.blend` files.

---

## Features

- **Asset Scanner** — Finds PSK/PSKX mesh files via [Everything SDK](https://www.voidtools.com/) with instant search across game directories
- **Material Resolver** — Reads exported `.props.txt` metadata, follows material inheritance chains, and classifies textures by PBR slot (Base Color, Normal, ORM, Emissive, etc.)
- **Automatic Blender Processing** — Headless Blender subprocess imports meshes, builds full shader node graphs with proper texture wiring, and saves `.blend` files
- **VFS Unpacker** — Built-in [CUE4Parse](https://github.com/FabianFG/CUE4Parse) CLI mounts `.pak`/`.utoc` archives with AES decryption and exports meshes, textures, animations, audio, and material properties
- **Batch Queue** — Multi-asset processing with progress tracking, logging, and cancellation
- **Texture Preset System** — Configurable texture slot rules (suffixes, parameter names, wiring types) with per-material overrides
- **WWise Audio Support** — Scans AkAudioEvent assets, extracts WEM files, and converts to WAV/OGG via vgmstream
- **Blend Combiner** — Merge multiple `.blend` files into a single scene with grid layout
- **Multi-Profile** — Per-game configuration profiles (game paths, AES keys, UE version, export directories)
- **Themeable UI** — 4 built-in color schemes + custom scheme editor with PySide6/Qt6

---

## Screenshots

<!-- Add screenshots here -->
<!-- ![Main Window](docs/screenshots/main.png) -->

---

## Download

### Pre-built Release (Windows x64)

Download the latest release from the [Releases](../../releases) page:

1. Download `EfficientAssetRipper-win-x64.zip`
2. Extract to any folder
3. Run `EfficientAssetRipper.exe`

**Requirements for the pre-built release:**
- Windows 10/11 (x64)
- [.NET 8.0 Runtime](https://dotnet.microsoft.com/download/dotnet/8.0) (for CUE4ParseCLI)
- [Blender 4.0+](https://www.blender.org/download/) (for asset processing)
- [Everything](https://www.voidtools.com/) (must be running for asset search)

---

## Installation (from source)

### Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| [Python](https://python.org) | 3.10+ | Application runtime |
| [Blender](https://www.blender.org/download/) | 4.0+ | Headless mesh import & material wiring |
| [Everything](https://www.voidtools.com/) | 1.4+ | Fast file search (must be running) |
| [.NET SDK](https://dotnet.microsoft.com/download/dotnet/8.0) | 8.0+ | Build CUE4ParseCLI (optional — pre-built included in releases) |

### Steps

1. **Clone the repository**
   ```bash
   git clone https://github.com/YOUR_USERNAME/EfficientAssetRipper.git
   cd EfficientAssetRipper
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   ```

3. **Activate the virtual environment**

   Windows:
   ```powershell
   .\venv\Scripts\Activate.ps1
   ```
   Linux/macOS:
   ```bash
   source venv/bin/activate
   ```

4. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

5. **Build CUE4ParseCLI** (optional — only needed for the VFS unpacker)

   Windows:
   ```bash
   build_cli.bat
   ```
   Linux/macOS:
   ```bash
   ./build_cli.sh
   ```

6. **Run the application**
   ```bash
   python main.py
   ```

### Blender PSK Addon

EfficientAssetRipper requires a PSK import addon in Blender. The default is the built-in `io_scene_psk_psa` extension (Blender 4.0+). You can change the addon name in **Settings → Processing**.

---

## First-Time Setup

1. **Launch the app** — the splash screen will play, then the main window appears
2. **Create a profile** — Click **New** in the profile bar, name it after your game
3. **Configure paths** in **Settings**:
   - **Game Folder** — path to the game's content/pak directory
   - **Blender** — path to `blender.exe`
   - **Everything DLL** — path to `Everything64.dll` (usually `C:\Program Files\Everything\Everything64.dll`)
   - **Output Dir** — where `.blend` files will be saved
   - **CUE4Parse CLI** — path to `CUE4ParseCLI.exe` (in `cue4parse_cli/bin/publish/`)
4. **Scan** — Click the Scan button to discover assets
5. **Select & Process** — Check assets in the browser tree, add to queue, and process

---

## Building an EXE

A build script is included to package the application as a standalone Windows executable.

### Quick Build

```bash
build.bat
```

This will:
1. Install PyInstaller if needed
2. Build the Python application into a standalone exe
3. Copy data files (blender scripts, texture presets, fonts)
4. Build CUE4ParseCLI (if .NET SDK is installed)
5. Create `dist/EfficientAssetRipper-win-x64.zip`

### Manual Build

```bash
pip install pyinstaller
pyinstaller --onedir --windowed --name EfficientAssetRipper ^
    --hidden-import PySide6.QtWidgets ^
    --hidden-import PySide6.QtCore ^
    --hidden-import PySide6.QtGui ^
    --hidden-import PySide6.QtMultimedia ^
    --hidden-import PIL ^
    main.py

:: Copy data files alongside the exe
xcopy /Y /E /I data dist\EfficientAssetRipper\data
xcopy /Y /E /I fonts dist\EfficientAssetRipper\fonts
xcopy /Y /E /I blender dist\EfficientAssetRipper\blender
```

The output will be in `dist/EfficientAssetRipper/`.

---

## Project Structure

```
EfficientAssetRipper/
├── main.py                  # Application entry point
├── _base.py                 # Base directory resolver (source & frozen exe)
├── config.py                # QSettings-based configuration
├── requirements.txt         # Python dependencies
├── build.bat                # Windows exe build script
├── build_cli.bat            # CUE4ParseCLI build script (Windows)
├── build_cli.sh             # CUE4ParseCLI build script (Linux/macOS)
│
├── core/                    # Backend logic
│   ├── asset_scanner.py     # Asset discovery & material resolution
│   ├── blender_runner.py    # Headless Blender subprocess wrapper
│   ├── classifier.py        # Asset categorization by folder path
│   ├── everything.py        # Everything SDK ctypes wrapper
│   ├── job_manager.py       # Batch processing queue (QThread)
│   ├── profile_manager.py   # Per-game profile CRUD
│   ├── props_parser.py      # UE .props.txt parser (JSON + legacy text)
│   ├── texture_resolver.py  # Texture classification & file lookup
│   └── unpacker.py          # CUE4ParseCLI NDJSON IPC wrapper
│
├── gui/                     # PySide6 UI
│   ├── main_window.py       # Central window orchestrator
│   ├── asset_browser.py     # Hierarchical asset tree with filtering
│   ├── audio_previewer.py   # Audio playback panel
│   ├── blend_combiner.py    # Multi-blend merge tool
│   ├── color_schemes.py     # Built-in + custom color scheme registry
│   ├── log_viewer.py        # Color-coded log display
│   ├── profile_bar.py       # Profile selector toolbar
│   ├── psk_picker.py        # PSK file browser with Everything search
│   ├── queue_panel.py       # Processing queue table
│   ├── settings_panel.py    # Configuration dialog (paths, processing, appearance)
│   ├── splash.py            # Animated startup overlay
│   ├── text_viewer.py       # Read-only text/JSON viewer
│   ├── tga_previewer.py     # TGA/PNG image viewer with zoom
│   ├── theme.py             # Centralized theming (palette, QSS, fonts)
│   ├── unpacker_panel.py    # VFS browser & asset export UI
│   └── widgets.py           # Reusable widgets (zoomable tree, collapsible sections)
│
├── blender/                 # Headless Blender scripts (run as subprocesses)
│   ├── process_asset.py     # Import PSK → wire materials → save .blend
│   ├── material_setup.py    # Shader node graph construction
│   └── combine_blends.py    # Merge multiple .blend files
│
├── cue4parse_cli/           # .NET 8.0 CLI tool
│   ├── Program.cs           # NDJSON IPC server for UE archive operations
│   └── CUE4ParseCLI.csproj  # Project file (CUE4Parse + Newtonsoft.Json)
│
├── data/
│   └── texture_presets.json # Texture slot rules & material overrides
│
├── fonts/                   # Custom font directory (drop .ttf/.otf files)
│
├── profiles/                # Per-game config (gitignored)
├── cache/                   # Scan result cache (gitignored)
├── outputs/                 # Generated .blend files (gitignored)
└── logs/                    # Processing logs (gitignored)
```

---

## Texture Wiring

EfficientAssetRipper uses a preset system to wire textures to Blender shader nodes. The default preset (`default_pbr`) handles standard PBR materials:

| Texture Slot | Suffixes | Wiring Type |
|-------------|----------|-------------|
| Base Color | `_C`, `_D`, `_Albedo`, `_CS` | Direct → Base Color |
| Normal | `_N`, `_Normal` | Normal Map node → Normal |
| ORM | `_ORM` | Split channels: R→AO, G→Roughness, B→Metallic |
| Emissive | `_E`, `_Emissive` | Direct → Emission Color |
| Opacity/Mask | `_M`, `_Mask`, `_A` | Direct → Alpha |
| Roughness | `_R`, `_Roughness` | Direct → Roughness |
| Metallic | `_MT`, `_Metallic` | Direct → Metallic |

Custom presets and per-material overrides can be defined in [data/texture_presets.json](data/texture_presets.json).

---

## CUE4ParseCLI

The built-in CLI tool communicates via NDJSON (newline-delimited JSON) over stdin/stdout. It supports:

- **Archive mounting** with AES key decryption
- **VFS browsing** of game content directories
- **Asset export**: Static/Skeletal Meshes (PSK/PSKX), Textures (PNG/TGA), Animations (PSA), Audio (WAV/OGG)
- **Material property serialization** to JSON
- **WWise audio** event scanning and WEM export with automatic naming
- **Oodle decompression** (auto-downloads if needed)
- **vgmstream integration** for WEM→WAV/OGG conversion

---

## Configuration

Settings are stored in the Windows registry via QSettings (`HKCU\Software\EfficientAssetRipper`). Per-game profiles are saved as JSON files in the `profiles/` directory.

Key settings:

| Setting | Description |
|---------|-------------|
| `game_folder` | Path to game content directory |
| `blender_exe` | Path to Blender executable |
| `everything_dll` | Path to Everything64.dll |
| `output_dir` | Default output directory for .blend files |
| `cue4parse_cli` | Path to CUE4ParseCLI.exe |
| `timeout_seconds` | Blender processing timeout (default: 120s) |
| `color_scheme` | UI theme (Dusk, Bloom, Slate, Midnight, or custom) |

---

## Dependencies

### Python
- [PySide6](https://pypi.org/project/PySide6/) ≥ 6.6.0 — Qt6 GUI framework
- [Pillow](https://pypi.org/project/Pillow/) ≥ 10.0.0 — Image loading (TGA/PNG/DDS preview)

### External Tools
- [Blender](https://www.blender.org/) 4.0+ — Headless PSK import and shader node construction
- [Everything](https://www.voidtools.com/) — Windows file search (must be running)
- [.NET 8.0 Runtime](https://dotnet.microsoft.com/download/dotnet/8.0) — Required for CUE4ParseCLI
- [vgmstream](https://vgmstream.org/) — WEM audio conversion (auto-downloaded by CUE4ParseCLI)

---

## License

This project is provided as-is for personal and educational use.
