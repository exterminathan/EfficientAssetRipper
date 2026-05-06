"""Unpacker panel — browse and extract Unreal Engine game archives."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.theme import install_combo_click_to_popup
from gui.widgets import CollapsibleSection, ZoomableTree

import config
from core import type_cache as type_cache_mod
from core.type_cache import TypeCache
from core.unpacker import UnpackerProcess

log = logging.getLogger(__name__)

# UE versions exposed in the dropdown — grouped by era, newest first
_UE_VERSIONS = [
    # UE5
    "GAME_UE5_5", "GAME_UE5_4", "GAME_UE5_3", "GAME_UE5_2",
    "GAME_UE5_1", "GAME_UE5_0",
    # UE4 (all minor versions)
    "GAME_UE4_27", "GAME_UE4_26", "GAME_UE4_25", "GAME_UE4_24",
    "GAME_UE4_23", "GAME_UE4_22", "GAME_UE4_21", "GAME_UE4_20",
    "GAME_UE4_19", "GAME_UE4_18", "GAME_UE4_17", "GAME_UE4_16",
    "GAME_UE4_15", "GAME_UE4_14", "GAME_UE4_13", "GAME_UE4_12",
    "GAME_UE4_11", "GAME_UE4_10", "GAME_UE4_9", "GAME_UE4_8",
    "GAME_UE4_7", "GAME_UE4_6", "GAME_UE4_5", "GAME_UE4_4",
    "GAME_UE4_3", "GAME_UE4_2", "GAME_UE4_1", "GAME_UE4_0",
    # Game-specific overrides (CUE4Parse EGame enum)
    "GAME_RocketLeague",
    "GAME_Valorant",
    "GAME_FortniteBR",
    "GAME_Fortnite",
    "GAME_PUBG",
    "GAME_Splitgate",
    "GAME_SeaOfThieves",
    "GAME_GearsOfWar4",
    "GAME_StateOfDecay2",
    "GAME_ArkSurvivalEvolved",
]

_PLACEHOLDER = "__placeholder__"


class UnpackerPanel(QWidget):
    """Left-tab panel for mounting UE archives and exporting assets."""

    psk_extracted = Signal(list)        # list[Path] of extracted PSK/PSKX files
    log_message = Signal(str, str)      # message, level
    props_viewed = Signal(str, str)     # (title, json_text) for Text Viewer
    audio_preview = Signal(str)         # local file path for Audio Preview tab
    tga_preview = Signal(str)           # local file path for TGA/image Preview tab
    mesh_preview = Signal(str)          # local .psk path for Mesh Preview tab
    version_mismatch = Signal(str)      # banner text for the log viewer

    def __init__(self, parent=None):
        super().__init__(parent)
        self._unpacker = UnpackerProcess(self)
        self._mounted = False
        self._exporting = False
        self._export_output_dir = ""
        self._ue_version_user_set = False  # track if user manually changed the version dropdown
        self._suppress_export_error_popup = False  # user opted to skip popups for the current CLI session

        # WWise audio data (populated by scan after mount)
        self._wwise_root = ""          # e.g. "Game/Content/WwiseAudio/"
        self._wwise_events_prefix = "" # e.g. "Game/Content/WwiseAudio/Events/"
        self._wwise_audio_map: dict[str, list[dict]] = {}  # event_folder → [{debug_name, wem_vfs_path, ...}]
        self._wwise_scan_done = False
        self._pending_wwise_export: tuple[list[dict], str] | None = None  # (entries, output_dir)
        self._audio_preview_temp_dir: "Path | None" = None   # set from main_window
        self._mesh_preview_temp_dir: "Path | None" = None    # set from main_window
        self._tga_preview_temp_dir: "Path | None" = None     # set from main_window
        # Pending temp-export-for-preview state. Tuple is (expected_path, kind)
        # where kind ∈ {"audio", "mesh", "texture"} so _on_export_done can
        # dispatch to the right preview signal once the file lands on disk.
        self._pending_temp_preview: "tuple[str, str, str] | None" = None

        # Type cache populated by `scan_types` after mount. None means the cache
        # hasn't loaded yet — filters that depend on it should treat unknown
        # rows as "any category" so the user isn't blinded during the scan.
        self._type_cache: "TypeCache | None" = None
        self._type_cache_fingerprint: str = ""
        self._type_scan_in_progress: bool = False

        # Folders auto-expanded by the search/filter. Cleared when search
        # empties so we re-collapse only what we opened, not user-opened nodes.
        self._auto_expanded: "set[QTreeWidgetItem]" = set()

        # Debounce timer: coalesces rapid text-input changes into one filter pass.
        self._filter_debounce = QTimer()
        self._filter_debounce.setSingleShot(True)
        self._filter_debounce.setInterval(120)
        self._filter_debounce.timeout.connect(self._filter_tree)

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # Item icons
    # ------------------------------------------------------------------

    # Per-category dot colors — chosen for contrast on dark backgrounds.
    _CAT_COLORS: dict[str, str] = {
        type_cache_mod.CATEGORY_MESH:      "#4a9eff",
        type_cache_mod.CATEGORY_TEXTURE:   "#4dca7a",
        type_cache_mod.CATEGORY_AUDIO:     "#f0a040",
        type_cache_mod.CATEGORY_MATERIAL:  "#b87fff",
        type_cache_mod.CATEGORY_ANIMATION: "#40d0d0",
        type_cache_mod.CATEGORY_OTHER:     "#888888",
    }

    # Lazy icon caches — populated on first use after QApplication exists.
    _icon_folder: "QIcon | None" = None
    _icon_package: "QIcon | None" = None
    _icon_audio_virtual: "QIcon | None" = None
    _icon_export_child: "dict[str, QIcon]" = {}

    @classmethod
    def _get_folder_icon(cls) -> QIcon:
        if cls._icon_folder is None:
            cls._icon_folder = QApplication.style().standardIcon(
                QStyle.StandardPixmap.SP_DirIcon
            )
        return cls._icon_folder

    @classmethod
    def _get_package_icon(cls) -> QIcon:
        """Small gray rounded-rect to indicate an unexpanded package file."""
        if cls._icon_package is None:
            cls._icon_package = cls._make_dot_icon("#aaaaaa", shape="rect")
        return cls._icon_package

    @classmethod
    def _get_audio_virtual_icon(cls) -> QIcon:
        if cls._icon_audio_virtual is None:
            cls._icon_audio_virtual = cls._make_dot_icon(
                cls._CAT_COLORS[type_cache_mod.CATEGORY_AUDIO]
            )
        return cls._icon_audio_virtual

    @classmethod
    def _get_export_child_icon(cls, export_type: str) -> QIcon:
        cat = type_cache_mod.category_for_export_type(export_type)
        if cat not in cls._icon_export_child:
            cls._icon_export_child[cat] = cls._make_dot_icon(cls._CAT_COLORS.get(cat, "#888888"))
        return cls._icon_export_child[cat]

    @classmethod
    def _icon_for_file(cls, name: str) -> "QIcon | None":
        """Return an icon for a non-folder VFS file based on extension."""
        lower = name.lower()
        if any(lower.endswith(e) for e in (".uasset", ".umap", ".upk")):
            return cls._get_package_icon()
        if any(lower.endswith(e) for e in (".psk", ".pskx")):
            return cls._get_export_child_icon("SkeletalMesh")
        if any(lower.endswith(e) for e in (".tga", ".png", ".dds", ".bmp", ".jpg", ".jpeg")):
            return cls._get_export_child_icon("Texture2D")
        if any(lower.endswith(e) for e in (".wav", ".ogg", ".wem", ".bnk", ".ewem")):
            return cls._get_export_child_icon("SoundWave")
        return None

    @staticmethod
    def _make_dot_icon(color: str, size: int = 12, shape: str = "circle") -> QIcon:
        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(color)))
        p.setPen(Qt.PenStyle.NoPen)
        if shape == "rect":
            p.drawRoundedRect(1, 2, size - 2, size - 3, 2, 2)
        else:
            p.drawEllipse(1, 1, size - 2, size - 2)
        p.end()
        return QIcon(px)

    # ------------------------------------------------------------------
    # Profile support
    # ------------------------------------------------------------------

    @property
    def is_exporting(self) -> bool:
        return self._exporting

    def load_from_profile(self, profile: dict) -> None:
        """Populate UI fields from a profile dict.

        If the user had archives mounted in this session, auto-remount on the
        next event-loop tick so the tree doesn't go visually empty after a
        Manage Profiles edit. The first-ever profile open never auto-mounts —
        the user must opt in by clicking Mount Archives once.
        """
        was_mounted = self._mounted
        self._ue_version_user_set = False
        self._suppress_export_error_popup = False
        self._game_dir_edit.setText(profile.get("game_dir", ""))

        ue = profile.get("ue_version", "GAME_UE5_4")
        self._ue_version_combo.blockSignals(True)
        idx = self._ue_version_combo.findText(ue)
        if idx >= 0:
            self._ue_version_combo.setCurrentIndex(idx)
        else:
            # Unknown version (e.g. a game-specific EGame enum value not in our list)
            self._ue_version_combo.addItem(ue)
            self._ue_version_combo.setCurrentText(ue)
        self._ue_version_combo.blockSignals(False)

        self._mappings_edit.setText(profile.get("mappings_path", ""))
        self._output_dir_edit.setText(profile.get("unpack_output_dir", ""))

        # AES keys
        self._keys_table.setRowCount(0)
        for k in profile.get("aes_keys", []):
            row = self._keys_table.rowCount()
            self._keys_table.insertRow(row)
            self._keys_table.setItem(row, 0, QTableWidgetItem(k.get("label", "")))
            self._keys_table.setItem(row, 1, QTableWidgetItem(k.get("guid", "")))
            self._keys_table.setItem(row, 2, QTableWidgetItem(k.get("key", "")))

        # Clear mounted state — user must re-mount after switching profiles
        if self._mounted:
            self._mounted = False
            self._tree.clear()
            self._tree.setEnabled(False)
            self._export_btn.setEnabled(False)
            self._export_folder_btn.setEnabled(False)
            self._mount_info.setText("")
            self._status_label.setText("Profile loaded — mount archives to browse")
            # Clear WWise state
            self._wwise_root = ""
            self._wwise_events_prefix = ""
            self._wwise_audio_map = {}
            self._wwise_scan_done = False
            self._pending_wwise_export = None
            # Drop any in-flight type-cache state — re-mount will re-scan or
            # load the cache for the new game.
            self._type_cache = None
            self._type_cache_fingerprint = ""
            self._type_scan_in_progress = False
            self._type_scan_bar.setVisible(False)
            self._auto_expanded.clear()

        # Auto-remount when the previous profile was already mounted in this
        # session. Defer to the next event-loop tick so the dialog finishes
        # closing before we touch the unpacker QProcess.
        if was_mounted and self._game_dir_edit.text().strip() and self._get_aes_keys():
            self._status_label.setText("Profile changed — remounting...")
            QTimer.singleShot(0, self._mount_archives)

    def collect_for_profile(self) -> dict:
        """Collect current UI state as a dict fragment for saving to a profile."""
        return {
            "game_dir": self._game_dir_edit.text().strip(),
            "ue_version": self._ue_version_combo.currentText(),
            "mappings_path": self._mappings_edit.text().strip(),
            "unpack_output_dir": self._output_dir_edit.text().strip(),
            "aes_keys": self._get_aes_keys(),
        }

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # We'll use a splitter so the mount/keys section vs the tree are resizable
        self._main_splitter = QSplitter(Qt.Orientation.Vertical)

        # ── Top section (mount + keys) ────────────────────────────────
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)

        # ── Mount controls ────────────────────────────────────────────
        mount_section = QGroupBox("Mount Archives")
        mount_layout = QVBoxLayout()

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Game folder:"))
        self._game_dir_edit = QLineEdit()
        self._game_dir_edit.setPlaceholderText("Path to game content folder (.pak, .upk, or loose content files)")
        row1.addWidget(self._game_dir_edit)
        self._browse_game_btn = QPushButton("Browse...")
        self._browse_game_btn.setFixedWidth(80)
        self._browse_game_btn.clicked.connect(self._browse_game_dir)
        row1.addWidget(self._browse_game_btn)
        mount_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("UE Version:"))
        self._ue_version_combo = QComboBox()
        self._ue_version_combo.setEditable(True)
        self._ue_version_combo.addItems(_UE_VERSIONS)
        install_combo_click_to_popup(self._ue_version_combo)
        saved_ver = config.get("unpack_ue_version")
        if saved_ver in _UE_VERSIONS:
            self._ue_version_combo.setCurrentText(saved_ver)
        else:
            self._ue_version_combo.setCurrentText("GAME_UE5_4")
        self._ue_version_combo.setFixedWidth(200)
        row2.addWidget(self._ue_version_combo)

        row2.addWidget(QLabel("Mappings:"))
        self._mappings_edit = QLineEdit()
        self._mappings_edit.setPlaceholderText("Optional .usmap file")
        row2.addWidget(self._mappings_edit)
        self._browse_mappings_btn = QPushButton("...")
        self._browse_mappings_btn.setFixedWidth(30)
        self._browse_mappings_btn.clicked.connect(self._browse_mappings)
        row2.addWidget(self._browse_mappings_btn)

        row2.addStretch()
        self._mount_btn = QPushButton("Mount Archives")
        self._mount_btn.setProperty("cssClass", "success")
        self._mount_btn.clicked.connect(self._mount_archives)
        row2.addWidget(self._mount_btn)
        mount_layout.addLayout(row2)

        # ── AES Keys (inside mount section) ───────────────────────────
        keys_label = QLabel("AES Keys")

        mount_layout.addWidget(keys_label)

        self._keys_table = QTableWidget()
        self._keys_table.setColumnCount(3)
        self._keys_table.setHorizontalHeaderLabels(["Label", "GUID", "Key (hex)"])
        self._keys_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self._keys_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self._keys_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._keys_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._keys_table.verticalHeader().setVisible(False)
        self._keys_table.setMaximumHeight(150)
        mount_layout.addWidget(self._keys_table)

        keys_btns = QHBoxLayout()
        add_key_btn = QPushButton("Add Key")
        add_key_btn.clicked.connect(self._add_key_row)
        keys_btns.addWidget(add_key_btn)
        remove_key_btn = QPushButton("Remove Selected")
        remove_key_btn.clicked.connect(self._remove_selected_key)
        keys_btns.addWidget(remove_key_btn)
        keys_btns.addStretch()
        mount_layout.addLayout(keys_btns)

        mount_section.setLayout(mount_layout)
        top_layout.addWidget(mount_section)

        # Load saved keys
        self._load_keys_from_config()

        self._main_splitter.addWidget(top_widget)

        # ── Bottom section (search + tree + export + progress) ─────────
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        # ── Search row ────────────────────────────────────────────────
        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter tree by name...")
        self._search.textChanged.connect(self._filter_debounce.start)
        search_row.addWidget(self._search, 1)

        bottom_layout.addLayout(search_row)

        # ── Filters (asset-type categories + type-contains) ───────────
        filters_section = CollapsibleSection("Filters", start_expanded=True)
        filters_layout = QVBoxLayout()
        filters_layout.setContentsMargins(0, 2, 0, 2)
        filters_layout.setSpacing(4)

        # Row 1: category checkboxes — all checked by default.
        cat_row = QHBoxLayout()
        cat_row.setContentsMargins(0, 0, 0, 0)
        cat_row.addWidget(QLabel("Categories:"))
        self._cat_checkboxes: dict[str, QCheckBox] = {}
        for cat_id, label in (
            (type_cache_mod.CATEGORY_MESH, "Meshes"),
            (type_cache_mod.CATEGORY_TEXTURE, "Textures"),
            (type_cache_mod.CATEGORY_AUDIO, "Audio"),
            (type_cache_mod.CATEGORY_MATERIAL, "Materials"),
            (type_cache_mod.CATEGORY_ANIMATION, "Animations"),
            (type_cache_mod.CATEGORY_OTHER, "Other"),
        ):
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.stateChanged.connect(self._filter_tree)
            cat_row.addWidget(cb)
            self._cat_checkboxes[cat_id] = cb
        cat_row.addStretch()
        filters_layout.addLayout(cat_row)

        # Row 2: free-text type-contains (substring match on raw export_type).
        type_row = QHBoxLayout()
        type_row.setContentsMargins(0, 0, 0, 0)
        type_row.addWidget(QLabel("Type contains:"))
        self._type_contains = QLineEdit()
        self._type_contains.setPlaceholderText(
            "e.g. AnimSequence, Material  (substring, blank=any)"
        )
        self._type_contains.setToolTip(
            "Show only items whose export_type contains this substring "
            "(case-insensitive). Combines with the category checkboxes."
        )
        self._type_contains.textChanged.connect(self._filter_debounce.start)
        type_row.addWidget(self._type_contains, 1)
        filters_layout.addLayout(type_row)

        filters_section.set_content_layout(filters_layout)
        bottom_layout.addWidget(filters_section)

        # ── Mount info (middle area) ──────────────────────────────────
        self._mount_info = QLabel("")
        bottom_layout.addWidget(self._mount_info)

        # ── Type-scan progress bar (only visible while background scan runs) ──
        self._type_scan_bar = QProgressBar()
        self._type_scan_bar.setTextVisible(True)
        self._type_scan_bar.setFormat("Scanning asset types: %v / %m packages")
        self._type_scan_bar.setMaximumHeight(18)
        self._type_scan_bar.setVisible(False)
        bottom_layout.addWidget(self._type_scan_bar)

        # ── VFS Tree ──────────────────────────────────────────────────
        self._tree = ZoomableTree()
        self._tree.setHeaderLabels(["Name"])
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.setEnabled(False)
        bottom_layout.addWidget(self._tree, stretch=1)

        # ── Export controls ───────────────────────────────────────────
        export_group = QGroupBox("Export")
        export_layout = QVBoxLayout(export_group)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Mounted Folder:"))
        self._output_dir_edit = QLineEdit()
        self._output_dir_edit.setPlaceholderText("Where mounted/exported files land — also where PSK Picker reads from")
        self._output_dir_edit.setText(config.get("unpack_output_dir"))
        out_row.addWidget(self._output_dir_edit)
        self._browse_output_btn = QPushButton("Browse...")
        self._browse_output_btn.setFixedWidth(80)
        self._browse_output_btn.clicked.connect(self._browse_output_dir)
        out_row.addWidget(self._browse_output_btn)
        export_layout.addLayout(out_row)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Formats:"))
        self._chk_mesh = QCheckBox("Meshes (PSK)")
        self._chk_mesh.setChecked(True)
        fmt_row.addWidget(self._chk_mesh)
        self._chk_texture = QCheckBox("Textures (PNG)")
        self._chk_texture.setChecked(True)
        fmt_row.addWidget(self._chk_texture)
        self._chk_anim = QCheckBox("Animations (PSA)")
        self._chk_anim.setChecked(True)
        fmt_row.addWidget(self._chk_anim)
        self._chk_audio = QCheckBox("Audio (OGG/WAV)")
        self._chk_audio.setChecked(True)
        fmt_row.addWidget(self._chk_audio)
        self._chk_props = QCheckBox("Props (TXT)")
        self._chk_props.setChecked(True)
        fmt_row.addWidget(self._chk_props)
        fmt_row.addStretch()
        export_layout.addLayout(fmt_row)

        btn_row = QHBoxLayout()
        self._export_btn = QPushButton("Export Selected")
        self._export_btn.setEnabled(False)
        self._export_btn.setProperty("cssClass", "accent")
        self._export_btn.clicked.connect(self._export_selected)
        btn_row.addWidget(self._export_btn)

        self._export_folder_btn = QPushButton("Export Folder")
        self._export_folder_btn.setEnabled(False)
        self._export_folder_btn.setProperty("cssClass", "accent")
        self._export_folder_btn.clicked.connect(self._export_folder)
        btn_row.addWidget(self._export_folder_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel_export)
        btn_row.addWidget(self._cancel_btn)

        btn_row.addStretch()

        self._handoff_btn = QPushButton("Send PSKs to Queue →")
        self._handoff_btn.setEnabled(False)
        self._handoff_btn.setToolTip("Find all extracted PSK/PSKX files in the output directory and add them to the Asset Browser queue")
        self._handoff_btn.clicked.connect(self._handoff_psks)
        btn_row.addWidget(self._handoff_btn)

        export_layout.addLayout(btn_row)
        bottom_layout.addWidget(export_group)

        # ── Progress bar ──────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._progress.setFormat("%v / %m (%p%)")
        bottom_layout.addWidget(self._progress)

        self._status_label = QLabel("Not mounted")
        bottom_layout.addWidget(self._status_label)

        self._main_splitter.addWidget(bottom_widget)
        self._main_splitter.setSizes([200, 500])
        layout.addWidget(self._main_splitter, stretch=1)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self):
        self._unpacker.initialized.connect(self._on_initialized)
        self._unpacker.browse_result.connect(self._on_browse_result)
        self._unpacker.progress.connect(self._on_progress)
        self._unpacker.export_done.connect(self._on_export_done)
        self._unpacker.props_result.connect(self._on_props_result)
        self._unpacker.exports_listed.connect(self._on_exports_listed)
        self._unpacker.wwise_scan_result.connect(self._on_wwise_scan_result)
        self._unpacker.types_scan_progress.connect(self._on_types_scan_progress)
        self._unpacker.types_scan_batch.connect(self._on_types_scan_batch)
        self._unpacker.types_scan_done.connect(self._on_types_scan_done)
        self._unpacker.warning.connect(self._on_warning)
        self._unpacker.version_warning.connect(self._on_version_warning)
        self._unpacker.version_detected.connect(self._on_version_detected)
        self._unpacker.error.connect(self._on_error)
        self._unpacker.process_ended.connect(self._on_process_ended)
        self._ue_version_combo.currentTextChanged.connect(self._on_ue_version_changed)
        self._ue_version_combo.editTextChanged.connect(self._on_ue_version_changed)

    # ------------------------------------------------------------------
    # Tree filtering (in-place hide/show for lazy-loaded VFS)
    # ------------------------------------------------------------------

    # Extension-based fallback for files we don't have export-type info on.
    _MESH_FILE_EXTS = (".psk", ".pskx")
    _TEXTURE_FILE_EXTS = (".tga", ".png", ".dds", ".bmp", ".jpg", ".jpeg")
    _AUDIO_FILE_EXTS = (".wav", ".ogg", ".wem", ".bnk", ".ewem")
    _PACKAGE_FILE_EXTS = (".uasset", ".upk", ".umap")

    def _row_categories(self, item: QTreeWidgetItem) -> "set[str] | None":
        """Return the categories this row belongs to, or None for 'unknown — pass'.

        - WWise virtual rows → audio
        - Export rows (UserRole+4) → from cached export_type taxonomy
        - Plain files → cached type info first, then file extension
        - Unscanned packages while a scan is in progress → None (fail open)
        - Folders → None (visibility decided by descendants)
        """
        if item.data(0, Qt.ItemDataRole.UserRole + 2):
            return {type_cache_mod.CATEGORY_AUDIO}

        export_type = item.data(0, Qt.ItemDataRole.UserRole + 4)
        if export_type:
            return {type_cache_mod.category_for_export_type(str(export_type))}

        if item.data(0, Qt.ItemDataRole.UserRole + 1):
            return None

        vfs_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
        lower = vfs_path.lower()

        if any(lower.endswith(ext) for ext in self._PACKAGE_FILE_EXTS):
            if self._type_cache is not None:
                cats = self._type_cache.categories_for_package(vfs_path)
                if cats:
                    return cats
            # During scan, uncached packages default to Other so category filters
            # remain meaningful (fail-open would let everything bleed into Mesh etc.)
            return {type_cache_mod.CATEGORY_OTHER}

        if lower.endswith(self._MESH_FILE_EXTS):
            return {type_cache_mod.CATEGORY_MESH}
        if lower.endswith(self._TEXTURE_FILE_EXTS):
            return {type_cache_mod.CATEGORY_TEXTURE}
        if lower.endswith(self._AUDIO_FILE_EXTS):
            return {type_cache_mod.CATEGORY_AUDIO}

        return {type_cache_mod.CATEGORY_OTHER}

    def _row_export_types(self, item: QTreeWidgetItem) -> "set[str] | None":
        """Return raw export_type strings for this row, or None for 'unknown'.

        Empty set means: known to have no export types (e.g. plain texture
        file). Used by the type-contains substring filter.
        """
        if item.data(0, Qt.ItemDataRole.UserRole + 2):
            return {"AkAudioEvent"}

        export_type = item.data(0, Qt.ItemDataRole.UserRole + 4)
        if export_type:
            return {str(export_type)}

        if item.data(0, Qt.ItemDataRole.UserRole + 1):
            return None  # folder

        vfs_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
        lower = vfs_path.lower()

        if any(lower.endswith(ext) for ext in self._PACKAGE_FILE_EXTS):
            if self._type_cache is not None:
                types = self._type_cache.export_types_for_package(vfs_path)
                if types:
                    return types
            if self._type_scan_in_progress:
                return None
        return set()

    def _filter_is_active(self, text: str, type_contains: str,
                          active_cats: set) -> bool:
        return bool(text) or bool(type_contains) or active_cats != type_cache_mod.ALL_CATEGORIES

    def _filter_tree(self):
        """Filter the VFS tree in-place, hiding non-matching items."""
        text = self._search.text().strip().lower()
        type_contains = self._type_contains.text().strip().lower()
        active_cats = {
            cat_id for cat_id, cb in self._cat_checkboxes.items() if cb.isChecked()
        }

        is_active = self._filter_is_active(text, type_contains, active_cats)

        # Batch all visibility changes to avoid per-item repaints stalling the
        # event loop on large trees.
        root = self._tree.invisibleRootItem()
        self._tree.setUpdatesEnabled(False)
        try:
            self._filter_tree_recursive(
                root, text, type_contains, active_cats,
                parent_name_match=False, is_active=is_active,
            )
        finally:
            self._tree.setUpdatesEnabled(True)

        # When no filter is active, restore folders we auto-opened.
        if not is_active and self._auto_expanded:
            self._tree.setUpdatesEnabled(False)
            try:
                for opened in list(self._auto_expanded):
                    try:
                        opened.setExpanded(False)
                    except RuntimeError:
                        pass  # item may have been deleted between filter calls
            finally:
                self._tree.setUpdatesEnabled(True)
            self._auto_expanded.clear()

    def _filter_tree_recursive(
        self,
        parent: QTreeWidgetItem,
        text: str,
        type_contains: str,
        active_cats: set,
        parent_name_match: bool,
        is_active: bool,
    ) -> bool:
        """Hide non-matching items. Returns True if any child is visible."""
        if not is_active:
            # Fast path: show everything without classifying each row.
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.isHidden():
                    child.setHidden(False)
                if child.data(0, Qt.ItemDataRole.UserRole + 1):  # is folder
                    self._filter_tree_recursive(
                        child, text, type_contains, active_cats,
                        parent_name_match=False, is_active=False,
                    )
            return True

        any_visible = False
        for i in range(parent.childCount()):
            child = parent.child(i)
            is_folder = bool(child.data(0, Qt.ItemDataRole.UserRole + 1))
            display = child.text(0)
            # Strip "[Type]" suffix so a search like "_TX" doesn't accidentally
            # match "[Texture2D]" rows just because the type label shares letters.
            base = display.rsplit("[", 1)[0].rstrip().lower()

            self_name_match = (not text) or (text in base)
            name_match = self_name_match or parent_name_match

            if is_folder:
                # Never auto-expand a lazy-loaded (placeholder-only) folder.
                # Expanding it fires itemExpanded → browse() → _on_browse_result
                # → _filter_tree(), cascading into thousands of CLI round-trips.
                # Show/hide the folder by name; the user can expand it manually.
                is_placeholder_only = (
                    child.childCount() == 1
                    and child.child(0).data(0, Qt.ItemDataRole.UserRole) == _PLACEHOLDER
                )
                if is_placeholder_only:
                    name_passes = bool(self_name_match or parent_name_match)
                    type_passes = True
                    if active_cats != type_cache_mod.ALL_CATEGORIES and self._type_cache is not None:
                        folder_path = child.data(0, Qt.ItemDataRole.UserRole) or ""
                        folder_cats = self._type_cache.categories_under_folder(folder_path)
                        # Only hide if we have indexed data; unknown folders stay visible.
                        if folder_cats:
                            type_passes = bool(folder_cats & active_cats)
                    visible = name_passes and type_passes
                    child.setHidden(not visible)
                    if visible:
                        any_visible = True
                    continue

                descendant_visible = self._filter_tree_recursive(
                    child, text, type_contains, active_cats,
                    parent_name_match=parent_name_match or self_name_match,
                    is_active=is_active,
                )
                visible = descendant_visible
                child.setHidden(not visible)
                if visible:
                    any_visible = True
                    if not child.isExpanded():
                        child.setExpanded(True)
                        self._auto_expanded.add(child)
                continue

            # Non-folder rows: combine name axis with category + type-contains.
            type_match = True
            if active_cats != type_cache_mod.ALL_CATEGORIES:
                cats = self._row_categories(child)
                if cats is not None and not (cats & active_cats):
                    type_match = False
            if type_match and type_contains:
                types = self._row_export_types(child)
                if types is not None:
                    if not any(type_contains in t.lower() for t in types):
                        type_match = False

            visible = name_match and type_match
            child.setHidden(not visible)
            if visible:
                any_visible = True

        return any_visible

    # ------------------------------------------------------------------
    # Mount
    # ------------------------------------------------------------------

    def _mount_archives(self):
        cli_path = config.get_cue4parse_cli()
        if not cli_path:
            QMessageBox.warning(self, "CLI Not Configured",
                                "Set the CUE4ParseCLI path in Settings first.")
            return

        game_dir = self._game_dir_edit.text().strip()
        if not game_dir:
            QMessageBox.warning(self, "No Game Directory", "Enter the game content directory.")
            return

        ue_version = self._ue_version_combo.currentText()
        config.set("unpack_ue_version", ue_version)

        # Save keys to config before mounting
        self._save_keys_to_config()

        self._mount_btn.setEnabled(False)
        self._status_label.setText("Starting CUE4ParseCLI...")
        self._progress.setMaximum(0)  # indeterminate
        # Clear any prior version-mismatch banner — re-mount re-arms detection.
        self.version_mismatch.emit("")

        if not self._unpacker.is_running:
            if not self._unpacker.start(cli_path):
                self._mount_btn.setEnabled(True)
                self._status_label.setText("Failed to start CLI")
                self._progress.setMaximum(1)
                return

        aes_keys = self._get_aes_keys()
        mappings = self._mappings_edit.text().strip()
        self._unpacker.initialize(game_dir, aes_keys, ue_version, mappings)

    @Slot(int, int, int, int, int)
    def _on_initialized(self, archive_count: int, unmounted_count: int,
                        file_count: int, keys_submitted: int, loose_file_count: int = 0):
        # Defensive: clamp negative counts (CLI bug shouldn't take down the GUI)
        archive_count = max(0, archive_count)
        unmounted_count = max(0, unmounted_count)
        file_count = max(0, file_count)
        keys_submitted = max(0, keys_submitted)
        loose_file_count = max(0, loose_file_count)

        self._mounted = True
        self._mount_btn.setEnabled(True)
        self._tree.setEnabled(True)
        self._export_btn.setEnabled(True)
        self._export_folder_btn.setEnabled(True)
        self._progress.setMaximum(1)
        self._progress.setValue(0)

        info = f"Mounted: {archive_count} archives, {file_count:,} files"
        if loose_file_count:
            info += f" ({loose_file_count:,} loose .upk/.wem/.ewem/.bnk)"
        if unmounted_count:
            info += f" ({unmounted_count} archives need AES keys)"
        if keys_submitted:
            info += f", {keys_submitted} AES key(s) applied"
        self._mount_info.setText(info)
        self._status_label.setText("Ready — browse the tree and select assets to export")
        self.log_message.emit(info, "success")

        # Populate root of VFS tree
        self._tree.clear()
        self._auto_expanded.clear()
        self._unpacker.browse("")

        # Trigger WWise audio event scan (CLI returns quickly if no WWiseAudio folder)
        self._wwise_root = ""
        self._wwise_events_prefix = ""
        self._wwise_audio_map = {}
        self._wwise_scan_done = False
        self._unpacker.scan_wwise_events()

        # Try to reuse a cached type map for this game; otherwise scan fresh.
        self._begin_type_cache_load()

    # ------------------------------------------------------------------
    # Type cache (asset-class index used by the Filters section)
    # ------------------------------------------------------------------

    def _begin_type_cache_load(self) -> None:
        """Load the type cache from disk if available; else trigger a scan."""
        game_dir = self._game_dir_edit.text().strip()
        ue_version = self._ue_version_combo.currentText()
        fingerprint = type_cache_mod.compute_fingerprint(game_dir, ue_version)
        self._type_cache_fingerprint = fingerprint

        cached = TypeCache.load(fingerprint)
        if cached is not None:
            self._type_cache = cached
            self._type_scan_in_progress = False
            self._type_scan_bar.setVisible(False)
            self.log_message.emit(
                f"Type cache loaded ({len(cached.entries):,} packages)", "info"
            )
            self._filter_tree()
            return

        # Cache miss — start a fresh scan. The user can browse/search while
        # it runs; filters that depend on the cache treat unknown rows as
        # "any category" until done.
        self._type_cache = TypeCache()
        self._type_scan_in_progress = True
        self._type_scan_log_pct = -1  # tracks last logged 10% milestone
        self._type_scan_bar.setValue(0)
        self._type_scan_bar.setMaximum(0)  # indeterminate until total is known
        self._type_scan_bar.setVisible(True)
        self.log_message.emit("Asset-type scan started", "info")
        self._unpacker.scan_types()

    @Slot(int, int)
    def _on_types_scan_progress(self, current: int, total: int) -> None:
        if total > 0:
            self._type_scan_bar.setMaximum(total)
            self._type_scan_bar.setValue(current)
            # Log every 10% milestone to keep the user informed without spamming.
            pct = (current * 10) // total
            if pct > getattr(self, "_type_scan_log_pct", -1):
                self._type_scan_log_pct = pct
                self.log_message.emit(
                    f"Asset-type scan: {current:,}/{total:,} packages ({pct * 10}%)", "info"
                )

    @Slot(list, bool)
    def _on_types_scan_batch(self, entries: list, is_final: bool) -> None:
        if self._type_cache is None:
            self._type_cache = TypeCache()
        self._type_cache.add_batch(entries)

    @Slot(int, int)
    def _on_types_scan_done(self, error_count: int, total_packages: int) -> None:
        self._type_scan_in_progress = False
        self._type_scan_bar.setVisible(False)
        if self._type_cache is None:
            self._type_cache = TypeCache()
        self._type_cache.error_count = error_count
        self._type_cache.total_packages = total_packages

        try:
            self._type_cache.save(self._type_cache_fingerprint)
        except OSError as exc:
            log.warning("Could not save type cache: %s", exc)

        self._type_cache.rebuild_folder_index()

        msg = f"Asset-type scan complete ({len(self._type_cache.entries):,} packages"
        if error_count:
            msg += f", {error_count} unreadable"
        msg += ")"
        self.log_message.emit(msg, "info")

        # Apply any active filter now that we have full type info.
        self._filter_tree()

    # ------------------------------------------------------------------
    # WWise audio scan
    # ------------------------------------------------------------------

    @Slot(dict)
    def _on_wwise_scan_result(self, result: dict):
        """Handle the result of scanning AkAudioEvent assets."""
        self._wwise_scan_done = True
        if not result.get("found"):
            return

        self._wwise_root = result.get("wwise_root", "")
        self._wwise_events_prefix = result.get("events_prefix", "")
        total_audio = result.get("total_audio", 0)
        total_events = result.get("total_events", 0)

        # Build audio map: event_folder → list of audio entries
        self._wwise_audio_map = {}
        for entry in result.get("audio", []):
            folder = entry.get("event_folder", "")
            self._wwise_audio_map.setdefault(folder, []).append(entry)

        msg = f"WWise scan: {total_audio} audio files from {total_events} events"
        self._status_label.setText(msg)
        self.log_message.emit(msg, "success")

        # If the WwiseAudio folder is already visible in the tree, refresh it
        self._refresh_wwise_tree_node()

    def _refresh_wwise_tree_node(self):
        """If any WwiseAudio node is in the tree, clear and re-browse it to apply filtering."""
        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            self._refresh_wwise_recursive(child)

    def _refresh_wwise_recursive(self, item: QTreeWidgetItem):
        """Walk tree to find and refresh any WwiseAudio folder node."""
        name = item.text(0)
        is_folder = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if is_folder and name.lower() == "wwiseaudio":
            # Clear children and re-browse to apply filtering
            vfs_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
            while item.childCount():
                item.removeChild(item.child(0))
            # Add placeholder and re-browse
            placeholder = QTreeWidgetItem(["Loading..."])
            placeholder.setData(0, Qt.ItemDataRole.UserRole, _PLACEHOLDER)
            item.addChild(placeholder)
            self._unpacker.browse(vfs_path)
            return
        # Recurse into children
        for i in range(item.childCount()):
            self._refresh_wwise_recursive(item.child(i))

    @Slot(str, list)
    def _on_browse_result(self, path: str, entries: list):
        if not path:
            # Root level
            parent = self._tree.invisibleRootItem()
        else:
            parent = self._find_tree_item(path)
            if parent is None:
                return

        # Remove placeholder children
        for i in range(parent.childCount() - 1, -1, -1):
            child = parent.child(i)
            if child.data(0, Qt.ItemDataRole.UserRole) == _PLACEHOLDER:
                parent.removeChild(child)

        # ── WWise filtering: hide Event and Media folders ─────────────
        if self._wwise_scan_done and self._wwise_root:
            wwise_parent = self._wwise_root.rstrip("/")
            if path == wwise_parent:
                entries = [e for e in entries
                           if not (e.get("is_folder") and
                                   e["name"].lower() in ("event", "media"))]

        # Sort: folders first, then files
        folders = sorted([e for e in entries if e.get("is_folder")], key=lambda e: e["name"].lower())
        files = sorted([e for e in entries if not e.get("is_folder")], key=lambda e: e["name"].lower())

        for entry in folders:
            item = QTreeWidgetItem([entry["name"]])
            item.setData(0, Qt.ItemDataRole.UserRole, self._make_path(path, entry["name"]))
            item.setData(0, Qt.ItemDataRole.UserRole + 1, True)  # is_folder
            item.setIcon(0, self._get_folder_icon())
            # Add placeholder for lazy loading
            placeholder = QTreeWidgetItem(["Loading..."])
            placeholder.setData(0, Qt.ItemDataRole.UserRole, _PLACEHOLDER)
            item.addChild(placeholder)
            parent.addChild(item)

        for entry in files:
            item = QTreeWidgetItem([entry["name"]])
            item.setData(0, Qt.ItemDataRole.UserRole, self._make_path(path, entry["name"]))
            item.setData(0, Qt.ItemDataRole.UserRole + 1, False)  # is_folder
            icon = self._icon_for_file(entry["name"])
            if icon:
                item.setIcon(0, icon)
            parent.addChild(item)

        # ── WWise: inject virtual audio items into Events subfolders ──
        if self._wwise_scan_done and self._wwise_events_prefix:
            events_path = self._wwise_events_prefix.rstrip("/")
            if path == events_path or path.startswith(events_path + "/"):
                # Compute the relative folder within Events
                if path == events_path:
                    rel_folder = ""
                else:
                    rel_folder = path[len(events_path) + 1:]

                audio_format = config.get("export_audio_format") or "wav"
                audio_entries = self._wwise_audio_map.get(rel_folder, [])
                for audio in audio_entries:
                    display = f"{audio['debug_name']}.{audio_format}"
                    item = QTreeWidgetItem([display])
                    # Store the wem VFS path for export lookup
                    item.setData(0, Qt.ItemDataRole.UserRole, audio["wem_vfs_path"])
                    item.setData(0, Qt.ItemDataRole.UserRole + 1, False)  # not a folder
                    item.setData(0, Qt.ItemDataRole.UserRole + 2, audio)  # audio metadata
                    item.setForeground(0, Qt.GlobalColor.cyan)
                    item.setIcon(0, self._get_audio_virtual_icon())
                    parent.addChild(item)

        # Re-apply filter only when one is active — the full tree walk is
        # expensive and unnecessary when everything is already visible.
        if self._filter_is_active(
            self._search.text().strip().lower(),
            self._type_contains.text().strip().lower(),
            {k for k, cb in self._cat_checkboxes.items() if cb.isChecked()},
        ):
            self._filter_tree()

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    _IMAGE_EXTS = frozenset({".tga", ".png", ".dds", ".bmp", ".jpg", ".jpeg"})
    _AUDIO_EXTS = frozenset({".wav", ".ogg", ".wem", ".bnk", ".ewem"})
    _MESH_EXTS = frozenset({".psk", ".pskx"})
    _PACKAGE_EXTS_FOR_MESH = (".uasset", ".upk", ".umap")

    # Map UE class names returned by CUE4ParseCLI's list_exports to the
    # preview kind. Anything not listed here falls back to "unknown" and only
    # offers Preview Properties.
    _MESH_EXPORT_TYPES = frozenset({"SkeletalMesh", "StaticMesh"})
    _TEXTURE_EXPORT_TYPES = frozenset({"Texture2D", "TextureCube", "Texture2DArray"})
    _AUDIO_EXPORT_TYPES = frozenset({"SoundCue", "SoundWave", "AkAudioEvent"})

    def _classify_row(self, item: QTreeWidgetItem) -> str:
        """Identify what kind of preview a tree row supports.

        Returns one of "mesh", "texture", "audio", "package", "unknown".
        - audio_data set            → "audio" (WWise virtual entry)
        - export_type set           → mapped to mesh/texture/audio or "unknown"
        - file with mesh/audio/img extension → that type
        - .uasset/.upk/.umap that hasn't been expanded yet → "package"
          (the user can preview as mesh/texture/audio without expanding;
          the CLI runs a single-format export and we surface what landed)
        - everything else           → "unknown"
        """
        if item.data(0, Qt.ItemDataRole.UserRole + 2):
            return "audio"

        export_type = item.data(0, Qt.ItemDataRole.UserRole + 4)
        if export_type:
            if export_type in self._MESH_EXPORT_TYPES:
                return "mesh"
            if export_type in self._TEXTURE_EXPORT_TYPES:
                return "texture"
            if export_type in self._AUDIO_EXPORT_TYPES:
                return "audio"
            return "unknown"

        vfs_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
        suffix = Path(vfs_path.lower()).suffix
        if suffix in self._AUDIO_EXTS:
            return "audio"
        if suffix in self._IMAGE_EXTS:
            return "texture"
        if suffix in self._MESH_EXTS:
            return "mesh"
        if suffix in self._PACKAGE_EXTS_FOR_MESH:
            return "package"
        return "unknown"

    def _show_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if item is None:
            return
        self._popup_context_menu(item, self._tree.viewport().mapToGlobal(pos))

    def _popup_context_menu(self, item: QTreeWidgetItem, global_pos):
        """Build and show the right-click menu for *item*. Split out from
        `_show_context_menu` so tests can drive it directly without poking
        the QTreeWidget's internal hit-testing."""
        vfs_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
        if vfs_path == _PLACEHOLDER:
            return

        is_folder = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if is_folder:
            return

        audio_data = item.data(0, Qt.ItemDataRole.UserRole + 2)
        kind = self._classify_row(item)

        menu = QMenu(self)

        # Type-specific preview action(s) — temp-exports if needed, so we
        # never have to disable the menu item with a "must export first"
        # tooltip.
        def _add_mesh():
            act = QAction("Preview Mesh", self)
            act.triggered.connect(lambda checked=False, p=vfs_path: self._preview_mesh_vfs(p))
            menu.addAction(act)

        def _add_texture():
            act = QAction("Preview Texture", self)
            act.triggered.connect(lambda checked=False, p=vfs_path: self._preview_texture_vfs(p))
            menu.addAction(act)

        def _add_audio():
            act = QAction("Preview Audio", self)
            if audio_data:
                act.triggered.connect(lambda checked=False, d=audio_data: self._try_audio_preview(d))
            else:
                act.triggered.connect(lambda checked=False, p=vfs_path: self._preview_audio_vfs(p))
            menu.addAction(act)

        if kind == "mesh":
            _add_mesh()
        elif kind == "texture":
            _add_texture()
        elif kind == "audio":
            _add_audio()
        elif kind == "package":
            # If the package has been expanded, its children carry export_type
            # data. Show only the preview buttons matching what's actually
            # inside. If unexpanded (only the placeholder child exists), we
            # don't know yet — fall back to offering all three and let the
            # temp-export sort it out.
            is_unexpanded = (
                item.childCount() == 0
                or (
                    item.childCount() == 1
                    and item.child(0).data(0, Qt.ItemDataRole.UserRole) == _PLACEHOLDER
                )
            )
            if is_unexpanded:
                _add_mesh()
                _add_texture()
                _add_audio()
            else:
                child_kinds = {
                    self._classify_row(item.child(i))
                    for i in range(item.childCount())
                }
                if "mesh" in child_kinds:
                    _add_mesh()
                if "texture" in child_kinds:
                    _add_texture()
                if "audio" in child_kinds:
                    _add_audio()

        # Properties always available — the CLI's get_props returns inline
        # JSON regardless of asset type, so this never has to disable.
        act_props = QAction("Preview Properties", self)
        act_props.triggered.connect(lambda checked=False, p=vfs_path: self._preview_props_ctx(p))
        menu.addAction(act_props)

        menu.exec(global_pos)

    def _find_local_mesh(self, vfs_path: str) -> "Path | None":
        """Locate an exported PSK/PSKX for *vfs_path* if one is on disk.

        Mirrors `_find_local_file` but specifically targets the .psk → .uasset
        extension swap CUE4Parse performs on mesh exports.
        """
        if not self._export_output_dir:
            return None
        out = Path(self._export_output_dir)
        suffix = Path(vfs_path.lower()).suffix

        if suffix in self._MESH_EXTS:
            candidates = [out / vfs_path.lstrip("/")]
            name = vfs_path.rsplit("/", 1)[-1]
            candidates.append(out / name)
        else:
            stem_path = out / vfs_path.lstrip("/")
            name = vfs_path.rsplit("/", 1)[-1]
            candidates = []
            for ext in (".psk", ".pskx"):
                candidates.append(stem_path.with_suffix(ext))
                candidates.append((out / name).with_suffix(ext))

        for c in candidates:
            if c.is_file():
                return c
        return None

    def _find_local_file(self, vfs_path: str) -> "Path | None":
        """Try to locate an exported VFS file on disk.

        Handles the common case where the export changes the extension
        (e.g. .wem → .wav / .ogg, .uasset → .psk, .ubulk → .png).
        """
        if not self._export_output_dir:
            return None
        out = Path(self._export_output_dir)

        # Build candidate stems with possible alternate extensions
        audio_format = config.get("export_audio_format") or "wav"
        texture_format = config.get("export_texture_format") or "png"
        _ALT_EXT: dict[str, list[str]] = {
            ".wem": [f".{audio_format}", ".wav", ".ogg"],
            ".bnk": [f".{audio_format}", ".wav", ".ogg"],
        }

        # Collect candidate paths (original + alt extensions)
        candidates: list[Path] = []
        for base_path in [out / vfs_path.lstrip("/")]:
            candidates.append(base_path)
            ext = base_path.suffix.lower()
            for alt in _ALT_EXT.get(ext, []):
                candidates.append(base_path.with_suffix(alt))

        # Also try by filename only (flat export layout)
        name = vfs_path.rsplit("/", 1)[-1]
        for base_path in [out / name]:
            candidates.append(base_path)
            ext = base_path.suffix.lower()
            for alt in _ALT_EXT.get(ext, []):
                candidates.append(base_path.with_suffix(alt))

        for c in candidates:
            if c.is_file():
                return c
        return None

    # ------------------------------------------------------------------
    # Temp-export-for-preview
    # ------------------------------------------------------------------

    def _temp_dir_for_kind(self, kind: str) -> "Path | None":
        if kind == "audio":
            return self._audio_preview_temp_dir
        if kind == "mesh":
            return self._mesh_preview_temp_dir
        if kind == "texture":
            return self._tga_preview_temp_dir
        return None

    def _candidate_extensions_for_kind(self, kind: str) -> tuple[str, ...]:
        """Output extensions the CLI may produce for a given preview kind."""
        if kind == "audio":
            audio_format = config.get("export_audio_format") or "wav"
            return (f".{audio_format}", ".wav", ".ogg")
        if kind == "mesh":
            return (".psk", ".pskx")
        if kind == "texture":
            tex_format = config.get("export_texture_format") or "png"
            return (f".{tex_format}", ".png", ".tga", ".dds")
        return ()

    def _find_in_temp(self, temp_dir: Path, vfs_path: str, kind: str) -> "Path | None":
        """Look for an already-previewed copy of *vfs_path* in *temp_dir*.

        Mirrors `_find_local_file`'s candidate enumeration but rooted at the
        previewer's temp dir. Tries the VFS-mirrored path first, then the
        flat filename layout, and within each tries every output extension
        the CLI might have written for this kind.
        """
        candidates: list[Path] = []
        nested = temp_dir / vfs_path.lstrip("/")
        flat = temp_dir / vfs_path.rsplit("/", 1)[-1]
        for base in (nested, flat):
            candidates.append(base)
            for ext in self._candidate_extensions_for_kind(kind):
                candidates.append(base.with_suffix(ext))
        for c in candidates:
            if c.is_file():
                return c
        return None

    def _predict_temp_output(self, temp_dir: Path, vfs_path: str, kind: str) -> Path:
        """Best guess at where the CLI will write the previewed file."""
        nested = temp_dir / vfs_path.lstrip("/")
        exts = self._candidate_extensions_for_kind(kind)
        if exts:
            return nested.with_suffix(exts[0])
        return nested

    def _kick_temp_export(self, vfs_path: str, kind: str):
        """Shared launcher for mesh / texture / audio temp-export-for-preview."""
        if self._exporting:
            self._status_label.setText("Export in progress — try again after it finishes")
            return
        if not self._mounted:
            self._status_label.setText("Mount an archive first")
            return
        temp_dir = self._temp_dir_for_kind(kind)
        if temp_dir is None:
            self._status_label.setText(f"{kind.capitalize()} preview temp dir not configured")
            return

        formats = {"mesh": False, "texture": False, "props": False,
                   "animation": False, "audio": False}
        formats[kind] = True

        expected = self._predict_temp_output(Path(temp_dir), vfs_path, kind)
        self._pending_temp_preview = (str(expected), kind, vfs_path)

        name = vfs_path.rsplit("/", 1)[-1] or vfs_path
        self._status_label.setText(f"Exporting for preview: {name}")
        self._begin_export()
        self._unpacker.export(
            [vfs_path], str(temp_dir), formats=formats,
            texture_format=config.get("export_texture_format") or "png",
            audio_format=config.get("export_audio_format") or "wav",
        )

    def _preview_audio_vfs(self, vfs_path: str):
        """Preview a VFS audio file — use local export if available, else temp-export."""
        local = self._find_local_file(vfs_path)
        if local:
            self.audio_preview.emit(str(local))
            return
        if self._audio_preview_temp_dir:
            cached = self._find_in_temp(self._audio_preview_temp_dir, vfs_path, "audio")
            if cached:
                self.audio_preview.emit(str(cached))
                return
        self._kick_temp_export(vfs_path, "audio")

    def _preview_mesh_vfs(self, vfs_path: str):
        """Preview a VFS mesh — local export if present, else temp-export."""
        local = self._find_local_mesh(vfs_path)
        if local:
            self.mesh_preview.emit(str(local))
            return
        if self._mesh_preview_temp_dir:
            cached = self._find_in_temp(self._mesh_preview_temp_dir, vfs_path, "mesh")
            if cached:
                self.mesh_preview.emit(str(cached))
                return
        self._kick_temp_export(vfs_path, "mesh")

    def _preview_texture_vfs(self, vfs_path: str):
        """Preview a VFS texture — local export if present, else temp-export."""
        local = self._find_local_file(vfs_path)
        if local:
            self.tga_preview.emit(str(local))
            return
        if self._tga_preview_temp_dir:
            cached = self._find_in_temp(self._tga_preview_temp_dir, vfs_path, "texture")
            if cached:
                self.tga_preview.emit(str(cached))
                return
        self._kick_temp_export(vfs_path, "texture")

    def _preview_props_ctx(self, vfs_path: str):
        """Load and display VFS file properties in the Text Viewer."""
        if self._exporting:
            self._status_label.setText("Export in progress — props will load when complete")
            return
        self._status_label.setText(f"Loading props for {vfs_path}...")
        self._unpacker.get_props(vfs_path)

    @Slot(QTreeWidgetItem, int)
    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        is_folder = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if is_folder:
            return

        # Virtual WWise audio items → audio preview
        audio_data = item.data(0, Qt.ItemDataRole.UserRole + 2)
        if audio_data:
            self._try_audio_preview(audio_data)
            return

        vfs_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
        if not vfs_path:
            return

        # Package files (.upk, .uasset, .umap) → list exports as child nodes
        lower = vfs_path.lower()
        _PACKAGE_EXTS = (".upk", ".uasset", ".umap")
        if any(lower.endswith(ext) for ext in _PACKAGE_EXTS):
            # Only fetch if we haven't already populated children (check for placeholder or no children)
            already_populated = item.data(0, Qt.ItemDataRole.UserRole + 3)
            if not already_populated:
                item.setData(0, Qt.ItemDataRole.UserRole + 3, True)  # mark as fetch-in-progress
                self._status_label.setText(f"Listing exports in {vfs_path}...")
                self._unpacker.list_exports(vfs_path)
            return

        # Regular audio files → audio preview (exported or temp)
        suffix = Path(lower).suffix
        if suffix in self._AUDIO_EXTS:
            self._preview_audio_vfs(vfs_path)
            return

        if self._exporting:
            self._status_label.setText("Export in progress \u2014 props will load when complete")
            return

        self._status_label.setText(f"Loading props for {vfs_path}...")
        self._unpacker.get_props(vfs_path)

    @Slot(str, list)
    def _on_exports_listed(self, path: str, exports: list):
        """Populate child nodes under a package item with its exports."""
        item = self._find_tree_item(path)
        if item is None:
            # Path may have a sub-path structure — try matching by the raw VFS path stored in UserRole
            item = self._find_tree_item_by_data(path)
        if item is None:
            self._status_label.setText(f"Could not find tree node for {path}")
            return

        if not exports:
            self._status_label.setText(f"No exports found in {path} (may be unsupported format)")
            return

        # Remove any existing children (placeholder or stale) before populating
        item.takeChildren()

        for exp in exports:
            name = exp.get("name", "?")
            export_type = exp.get("export_type", "Unknown")
            child = QTreeWidgetItem(item, [f"{name}  [{export_type}]"])
            # Store the parent package path so export can use it
            child.setData(0, Qt.ItemDataRole.UserRole, path)
            child.setData(0, Qt.ItemDataRole.UserRole + 1, False)  # not a folder
            # Stash the export_type so the right-click menu can pick the
            # correct "Preview …" action label without re-parsing display text.
            child.setData(0, Qt.ItemDataRole.UserRole + 4, export_type)
            child.setToolTip(0, f"Type: {export_type}\nPackage: {path}")
            child.setIcon(0, self._get_export_child_icon(export_type))

        item.setExpanded(True)
        self._status_label.setText(f"{len(exports)} export(s) in {path}")

        # Re-apply filter only when one is active (same reasoning as _on_browse_result).
        if self._filter_is_active(
            self._search.text().strip().lower(),
            self._type_contains.text().strip().lower(),
            {k for k, cb in self._cat_checkboxes.items() if cb.isChecked()},
        ):
            self._filter_tree()

    def _find_tree_item_by_data(self, vfs_path: str) -> QTreeWidgetItem | None:
        """Search the tree for an item whose UserRole data matches *vfs_path*."""
        stack = [self._tree.invisibleRootItem()]
        while stack:
            parent = stack.pop()
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.data(0, Qt.ItemDataRole.UserRole) == vfs_path:
                    return child
                if child.childCount():
                    stack.append(child)
        return None

    def _try_audio_preview(self, audio_data: dict):
        """Resolve a virtual WWise audio entry to a local file and emit audio_preview."""
        audio_format = config.get("export_audio_format") or "wav"
        evt_folder = audio_data.get("event_folder", "")
        full_folder = (
            (self._wwise_events_prefix.rstrip("/") + "/" + evt_folder).strip("/")
            if evt_folder
            else self._wwise_events_prefix.rstrip("/")
        )

        # Check already-exported location first
        if self._export_output_dir:
            local_path = Path(self._export_output_dir) / full_folder / f"{audio_data['debug_name']}.{audio_format}"
            if local_path.is_file():
                self.audio_preview.emit(str(local_path))
                return

        # Check temp dir
        if self._audio_preview_temp_dir:
            temp_path = self._audio_preview_temp_dir / full_folder / f"{audio_data['debug_name']}.{audio_format}"
            if temp_path.is_file():
                self.audio_preview.emit(str(temp_path))
                return

        # Export to temp dir for preview
        if self._exporting:
            self._status_label.setText("Export in progress \u2014 try again after it finishes")
            return
        if not self._mounted:
            self._status_label.setText("Mount an archive first")
            return
        if not self._audio_preview_temp_dir:
            self._status_label.setText("Audio not exported yet \u2014 export it first to preview")
            return

        temp_dir = str(self._audio_preview_temp_dir)
        entry = {
            "wem_vfs_path": audio_data["wem_vfs_path"],
            "target_name": audio_data["debug_name"],
            "target_folder": full_folder,
        }
        expected = self._audio_preview_temp_dir / full_folder / f"{audio_data['debug_name']}.{audio_format}"
        self._pending_temp_preview = (str(expected), "audio", audio_data["wem_vfs_path"])

        self._status_label.setText(f"Exporting for preview: {audio_data['debug_name']}")
        self._begin_export()
        self._unpacker.export_wwise_audio([entry], temp_dir, audio_format=audio_format)

    @Slot(str, list)
    def _on_props_result(self, path: str, exports: list):
        self._status_label.setText("Ready")
        text = json.dumps(exports, indent=2, ensure_ascii=False)
        name = path.rsplit("/", 1)[-1] if "/" in path else path
        self.props_viewed.emit(f"Properties — {name}", text)

    @Slot(QTreeWidgetItem)
    def _on_item_expanded(self, item: QTreeWidgetItem):
        # Check if this node only has the placeholder → need to fetch children
        if item.childCount() == 1 and item.child(0).data(0, Qt.ItemDataRole.UserRole) == _PLACEHOLDER:
            vfs_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
            self._unpacker.browse(vfs_path)

    def _find_tree_item(self, vfs_path: str) -> QTreeWidgetItem | None:
        """Walk the tree to find the item matching *vfs_path*."""
        parts = [p for p in vfs_path.split("/") if p]
        parent = self._tree.invisibleRootItem()
        for part in parts:
            found = False
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.text(0) == part:
                    parent = child
                    found = True
                    break
            if not found:
                return None
        return parent

    @staticmethod
    def _make_path(parent_path: str, name: str) -> str:
        if parent_path:
            return f"{parent_path}/{name}"
        return name

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _get_formats(self) -> dict[str, bool]:
        return {
            "mesh": self._chk_mesh.isChecked(),
            "texture": self._chk_texture.isChecked(),
            "animation": self._chk_anim.isChecked(),
            "audio": self._chk_audio.isChecked(),
            "props": self._chk_props.isChecked(),
        }

    def _export_selected(self):
        selected = self._tree.selectedItems()
        if not selected:
            QMessageBox.information(self, "No Selection", "Select one or more assets in the tree.")
            return

        output_dir = self._output_dir_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "No Output", "Set an output directory first.")
            return

        config.set("unpack_output_dir", output_dir)
        self._export_output_dir = output_dir

        # Split selected items into regular VFS files and virtual audio items
        regular_paths = []
        wwise_entries = []
        for item in selected:
            is_folder = item.data(0, Qt.ItemDataRole.UserRole + 1)
            vfs_path = item.data(0, Qt.ItemDataRole.UserRole)
            audio_data = item.data(0, Qt.ItemDataRole.UserRole + 2)
            if is_folder or not vfs_path:
                continue
            if audio_data:
                evt_folder = audio_data.get("event_folder", "")
                full_folder = (self._wwise_events_prefix.rstrip("/") + "/" + evt_folder).strip("/") if evt_folder else self._wwise_events_prefix.rstrip("/")
                wwise_entries.append({
                    "wem_vfs_path": audio_data["wem_vfs_path"],
                    "target_name": audio_data["debug_name"],
                    "target_folder": full_folder,
                })
            else:
                regular_paths.append(vfs_path)

        if not regular_paths and not wwise_entries:
            QMessageBox.information(self, "No Files", "Selection contains only folders. Use 'Export Folder' instead.")
            return

        self._begin_export()

        if regular_paths and wwise_entries:
            # Chain: send regular export first, queue wwise for after it completes
            self._pending_wwise_export = (wwise_entries, output_dir)
            self._unpacker.export(regular_paths, output_dir, self._get_formats(),
                                  texture_format=config.get("export_texture_format"),
                                  audio_format=config.get("export_audio_format"))
        elif regular_paths:
            self._unpacker.export(regular_paths, output_dir, self._get_formats(),
                                  texture_format=config.get("export_texture_format"),
                                  audio_format=config.get("export_audio_format"))
        else:
            self._unpacker.export_wwise_audio(
                wwise_entries, output_dir,
                audio_format=config.get("export_audio_format"))

    def _export_folder(self):
        selected = self._tree.selectedItems()
        if not selected:
            QMessageBox.information(self, "No Selection", "Select a folder in the tree.")
            return

        output_dir = self._output_dir_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "No Output", "Set an output directory first.")
            return

        # Only operate on selected folder items; ignore non-folder rows so a
        # mixed selection doesn't silently drop items.
        folder_paths: list[str] = []
        for item in selected:
            is_folder = item.data(0, Qt.ItemDataRole.UserRole + 1)
            vfs_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
            if is_folder and vfs_path:
                folder_paths.append(vfs_path)

        if not folder_paths:
            QMessageBox.information(
                self, "No Folders",
                "Selection contains no folders. Select one or more folder rows.",
            )
            return

        config.set("unpack_output_dir", output_dir)
        self._export_output_dir = output_dir

        self._begin_export()

        # Collect wwise audio entries if any selected folder is under Events.
        wwise_entries = []
        events_path = self._wwise_events_prefix.rstrip("/") if self._wwise_events_prefix else ""
        if self._wwise_scan_done and events_path:
            for vfs_path in folder_paths:
                if vfs_path == events_path or vfs_path.startswith(events_path + "/"):
                    rel_prefix = "" if vfs_path == events_path else vfs_path[len(events_path) + 1:]
                    for folder, entries in self._wwise_audio_map.items():
                        if rel_prefix == "" or folder == rel_prefix or folder.startswith(rel_prefix + "/"):
                            for audio in entries:
                                evt_folder = audio.get("event_folder", "")
                                full_folder = (events_path + "/" + evt_folder).strip("/") if evt_folder else events_path
                                wwise_entries.append({
                                    "wem_vfs_path": audio["wem_vfs_path"],
                                    "target_name": audio["debug_name"],
                                    "target_folder": full_folder,
                                })

        if wwise_entries:
            # Chain: send folder export first, queue wwise for after it completes
            self._pending_wwise_export = (wwise_entries, output_dir)

        # Export each selected folder. Only the *last* request will trigger
        # the chained WWise export; the CLI processes them sequentially.
        for vfs_path in folder_paths:
            self._unpacker.export_folder(
                vfs_path, output_dir, self._get_formats(),
                texture_format=config.get("export_texture_format"),
                audio_format=config.get("export_audio_format"),
            )

    def _begin_export(self):
        self._exporting = True
        self._export_btn.setEnabled(False)
        self._export_folder_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._handoff_btn.setEnabled(False)
        self._progress.setMaximum(0)
        self._progress.setValue(0)
        self._status_label.setText("Exporting...")

    def cancel_export(self):
        """Public entry point — used by MainWindow on profile switch / shutdown."""
        self._cancel_export()

    def _cancel_export(self):
        self._unpacker.cancel()
        self._cancel_btn.setEnabled(False)
        self._status_label.setText("Cancelling...")

    @Slot(int, int, str)
    def _on_progress(self, current: int, total: int, message: str):
        if total > 0:
            self._progress.setMaximum(total)
            self._progress.setValue(current)
        self._status_label.setText(message)

    @Slot(list, list)
    def _on_export_done(self, succeeded: list, failed: list):
        # If there's a pending wwise audio export, send it now (chained)
        if self._pending_wwise_export:
            entries, out_dir = self._pending_wwise_export
            self._pending_wwise_export = None
            self._status_label.setText(f"Exporting {len(entries)} audio files...")
            self._unpacker.export_wwise_audio(
                entries, out_dir,
                audio_format=config.get("export_audio_format"))
            return

        # Handle temp-export-for-preview (audio / mesh / texture)
        if self._pending_temp_preview:
            expected, kind, vfs_path = self._pending_temp_preview
            self._pending_temp_preview = None
            self._exporting = False
            self._export_btn.setEnabled(True)
            self._export_folder_btn.setEnabled(True)
            self._cancel_btn.setEnabled(False)

            signal_for_kind = {
                "audio": self.audio_preview,
                "mesh": self.mesh_preview,
                "texture": self.tga_preview,
            }.get(kind)

            resolved: "Path | None" = None
            if Path(expected).is_file():
                resolved = Path(expected)
            elif succeeded:
                # CLI may have written under a slightly different path (e.g.
                # extension swap). Prefer a succeeded entry whose extension
                # matches the kind; otherwise take the first succeeded file.
                exts = {e.lower() for e in self._candidate_extensions_for_kind(kind)}
                for p in succeeded:
                    cand = Path(p)
                    if cand.is_file() and (not exts or cand.suffix.lower() in exts):
                        resolved = cand
                        break
                if resolved is None:
                    cand = Path(succeeded[0])
                    if cand.is_file():
                        resolved = cand

            # Final fallback: the CLI writes flat (basename only) but
            # `succeeded` echoes the VFS input path, not the disk output.
            # Rescan the temp dir for any candidate matching this vfs_path.
            if resolved is None:
                temp_dir = self._temp_dir_for_kind(kind)
                if temp_dir is not None:
                    resolved = self._find_in_temp(Path(temp_dir), vfs_path, kind)

            if resolved is not None and signal_for_kind is not None:
                self._status_label.setText("Ready")
                signal_for_kind.emit(str(resolved))
            elif signal_for_kind is None:
                self._status_label.setText(f"Preview kind {kind!r} has no signal")
            else:
                self._status_label.setText(
                    f"This asset has no {kind} data to preview"
                )
            return

        self._exporting = False
        self._export_btn.setEnabled(True)
        self._export_folder_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._progress.setMaximum(len(succeeded) + len(failed))
        self._progress.setValue(len(succeeded) + len(failed))

        msg = f"Export complete: {len(succeeded)} succeeded, {len(failed)} failed"
        self._status_label.setText(msg)
        self.log_message.emit(msg, "success" if not failed else "warning")

        if failed:
            details = "\n".join(f"  {f.get('path', '?')}: {f.get('error', '?')}" for f in failed[:20])
            self.log_message.emit(f"Failed exports:\n{details}", "error")
            self._show_export_failure_popup(failed)

        # Enable hand-off if any PSKs were extracted
        if self._export_output_dir:
            psk_files = list(Path(self._export_output_dir).rglob("*.psk")) + \
                        list(Path(self._export_output_dir).rglob("*.pskx"))
            if psk_files:
                self._handoff_btn.setEnabled(True)
                self._handoff_btn.setText(f"Send {len(psk_files)} PSKs to Queue →")

    # ------------------------------------------------------------------
    # Hand-off to Asset Browser
    # ------------------------------------------------------------------

    def _handoff_psks(self):
        if not self._export_output_dir:
            return
        out_dir = Path(self._export_output_dir)
        psk_files = sorted(list(out_dir.rglob("*.psk")) + list(out_dir.rglob("*.pskx")))
        if not psk_files:
            QMessageBox.information(self, "No PSKs", "No PSK/PSKX files found in the output directory.")
            return
        self.psk_extracted.emit(psk_files)
        self.log_message.emit(f"Sent {len(psk_files)} PSK files to queue", "info")

    # ------------------------------------------------------------------
    # AES key management
    # ------------------------------------------------------------------

    def _add_key_row(self):
        row = self._keys_table.rowCount()
        self._keys_table.insertRow(row)
        self._keys_table.setItem(row, 0, QTableWidgetItem("Main"))
        self._keys_table.setItem(row, 1, QTableWidgetItem("00000000000000000000000000000000"))
        self._keys_table.setItem(row, 2, QTableWidgetItem(""))

    def _remove_selected_key(self):
        rows = sorted(set(idx.row() for idx in self._keys_table.selectedIndexes()), reverse=True)
        for row in rows:
            self._keys_table.removeRow(row)

    def _get_aes_keys(self) -> list[dict]:
        keys = []
        for row in range(self._keys_table.rowCount()):
            label = (self._keys_table.item(row, 0) or QTableWidgetItem()).text().strip()
            guid = (self._keys_table.item(row, 1) or QTableWidgetItem()).text().strip()
            key = (self._keys_table.item(row, 2) or QTableWidgetItem()).text().strip()
            if key:
                keys.append({"label": label, "guid": guid, "key": key})
        return keys

    def _save_keys_to_config(self):
        config.set("aes_keys", json.dumps(self._get_aes_keys()))

    def _load_keys_from_config(self):
        raw = config.get("aes_keys")
        if not raw:
            return
        try:
            keys = json.loads(raw)
        except json.JSONDecodeError:
            return
        for k in keys:
            row = self._keys_table.rowCount()
            self._keys_table.insertRow(row)
            self._keys_table.setItem(row, 0, QTableWidgetItem(k.get("label", "")))
            self._keys_table.setItem(row, 1, QTableWidgetItem(k.get("guid", "")))
            self._keys_table.setItem(row, 2, QTableWidgetItem(k.get("key", "")))

    # ------------------------------------------------------------------
    # Browse helpers
    # ------------------------------------------------------------------

    def _browse_game_dir(self):
        start = self._game_dir_edit.text().strip()
        path = QFileDialog.getExistingDirectory(self, "Select Game Content Folder", start)
        if path:
            self._game_dir_edit.setText(path)
            self._ue_version_user_set = False
            if self._unpacker.is_running:
                self._unpacker.detect_ue_version(path)

    def _browse_output_dir(self):
        start = self._output_dir_edit.text().strip()
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory", start)
        if path:
            self._output_dir_edit.setText(path)

    def _browse_mappings(self):
        start = self._mappings_edit.text().strip()
        path, _ = QFileDialog.getOpenFileName(self, "Select Mappings File", start,
                                               "USMAP Files (*.usmap);;All Files (*)")
        if path:
            self._mappings_edit.setText(path)

    # ------------------------------------------------------------------
    # Error / warning / cleanup
    # ------------------------------------------------------------------

    def _show_export_failure_popup(self, failed: list, *, fatal_message: str | None = None) -> None:
        """Surface export failures as a modal so the user can't miss them.

        The user can tick "Don't show again..." to silence further popups for
        the rest of this CLI session — useful when a version mismatch produces
        the same error across hundreds of items in one queue.
        """
        if self._suppress_export_error_popup:
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        if fatal_message is not None:
            box.setWindowTitle("Export Failed")
            box.setText("The export was halted by a fatal error.")
            box.setInformativeText(fatal_message)
        else:
            n = len(failed)
            box.setWindowTitle("Export Errors")
            box.setText(f"{n} item{'s' if n != 1 else ''} failed to export.")
            box.setInformativeText("Click 'Show Details' for the per-item error list.")
            preview_n = 50
            lines = [
                f"{f.get('path', '?')}\n   -> {f.get('error', '?')}"
                for f in failed[:preview_n]
            ]
            if n > preview_n:
                lines.append(f"... and {n - preview_n} more.")
            box.setDetailedText("\n\n".join(lines))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        chk = QCheckBox("Don't show this again until the CLI is re-mounted")
        box.setCheckBox(chk)
        box.exec()
        if chk.isChecked():
            self._suppress_export_error_popup = True

    @Slot(str)
    def _on_warning(self, message: str):
        self.log_message.emit(f"CUE4Parse warning: {message}", "warning")

    @Slot(str, str)
    def _on_version_warning(self, message: str, current_version: str):
        # Emit as a high-priority log line and surface a dismissible banner
        # near the log viewer so the user notices without needing to scroll.
        self.log_message.emit(f"Possible UE version mismatch: {message}", "error")
        banner = (
            f"Possible UE version mismatch — current: {current_version or '?'}. "
            f"{message}"
        )
        self.version_mismatch.emit(banner)

    @Slot(str, str, str)
    def _on_version_detected(self, suggested: str, source_exe: str, file_version: str):
        if suggested:
            # Auto-detected a version
            if not self._ue_version_user_set:
                self._ue_version_combo.setCurrentText(suggested)
            log_msg = f"Auto-detected UE version: {suggested} (from {source_exe}, FileVersion {file_version})"
        else:
            # Detection failed
            log_msg = f"UE version detection failed: {file_version}"
        self.log_message.emit(log_msg, "info")

    @Slot()
    def _on_ue_version_changed(self):
        self._ue_version_user_set = True

    @Slot(str)
    def _on_error(self, message: str):
        self._status_label.setText(f"Error: {message}")
        self._mount_btn.setEnabled(True)
        self._progress.setMaximum(1)
        self._progress.setValue(0)
        self.log_message.emit(f"CUE4Parse error: {message}", "error")
        if self._exporting:
            self._exporting = False
            self._export_btn.setEnabled(True)
            self._export_folder_btn.setEnabled(True)
            self._cancel_btn.setEnabled(False)
            self._show_export_failure_popup([], fatal_message=message)

    @Slot()
    def _on_process_ended(self):
        self._exporting = False
        self._mounted = False
        self._suppress_export_error_popup = False
        self._tree.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._export_folder_btn.setEnabled(False)
        self._mount_info.setText("")
        self._progress.setMaximum(1)
        self._progress.setValue(0)
        self._status_label.setText("CLI process ended — re-mount to continue")
        # Clear WWise state
        self._wwise_root = ""
        self._wwise_events_prefix = ""
        self._wwise_audio_map = {}
        self._wwise_scan_done = False
        self._pending_wwise_export = None

    def shutdown(self):
        """Stop the CLI process (called on app exit)."""
        self._save_keys_to_config()
        self._unpacker.stop()
