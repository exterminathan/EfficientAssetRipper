"""Unpacker panel — browse and extract Unreal Engine game archives."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QTableWidget,
    QTableWidgetItem,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.widgets import CollapsibleSection, ZoomableTree

import config
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
    version_mismatch = Signal(str)      # banner text for the log viewer

    def __init__(self, parent=None):
        super().__init__(parent)
        self._unpacker = UnpackerProcess(self)
        self._mounted = False
        self._exporting = False
        self._export_output_dir = ""
        self._ue_version_user_set = False  # track if user manually changed the version dropdown

        # WWise audio data (populated by scan after mount)
        self._wwise_root = ""          # e.g. "Game/Content/WwiseAudio/"
        self._wwise_events_prefix = "" # e.g. "Game/Content/WwiseAudio/Events/"
        self._wwise_audio_map: dict[str, list[dict]] = {}  # event_folder → [{debug_name, wem_vfs_path, ...}]
        self._wwise_scan_done = False
        self._pending_wwise_export: tuple[list[dict], str] | None = None  # (entries, output_dir)
        self._audio_preview_temp_dir: "Path | None" = None   # set from main_window
        self._pending_temp_preview: str | None = None         # expected temp file path after export

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # Profile support
    # ------------------------------------------------------------------

    @property
    def is_exporting(self) -> bool:
        return self._exporting

    def load_from_profile(self, profile: dict) -> None:
        """Populate UI fields from a profile dict (does NOT auto-mount)."""
        self._ue_version_user_set = False
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
        self._search.textChanged.connect(self._filter_tree)
        search_row.addWidget(self._search, 1)

        bottom_layout.addLayout(search_row)

        # ── Advanced filters (hidden by default) ──────────────────────
        adv_section = CollapsibleSection("Advanced filters", start_expanded=False)
        adv_layout = QHBoxLayout()
        adv_layout.setContentsMargins(0, 2, 0, 2)

        adv_layout.addWidget(QLabel("Extension:"))
        self._ext_filter = QLineEdit()
        self._ext_filter.setPlaceholderText("e.g. .uasset .umap  (space-separated, blank=all)")
        self._ext_filter.setToolTip("Show only files with these extensions (space-separated)")
        self._ext_filter.textChanged.connect(self._filter_tree)
        adv_layout.addWidget(self._ext_filter, 1)
        adv_section.set_content_layout(adv_layout)
        bottom_layout.addWidget(adv_section)

        # ── Mount info (middle area) ──────────────────────────────────
        self._mount_info = QLabel("")
        bottom_layout.addWidget(self._mount_info)

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

    def _filter_tree(self):
        """Filter the VFS tree in-place, hiding non-matching items."""
        text = self._search.text().lower().strip()
        ext_raw = self._ext_filter.text().strip().lower().split() if hasattr(self, "_ext_filter") else []
        root = self._tree.invisibleRootItem()
        self._filter_tree_recursive(root, text, ext_raw)

    def _filter_tree_recursive(self, parent: QTreeWidgetItem, text: str,
                                ext_filter: list[str]) -> bool:
        """Hide non-matching items. Returns True if any child is visible."""
        any_visible = False
        for i in range(parent.childCount()):
            child = parent.child(i)
            is_folder = child.data(0, Qt.ItemDataRole.UserRole + 1)
            name = child.text(0).lower()

            if is_folder:
                child_visible = self._filter_tree_recursive(child, text, ext_filter)
                if not text or text in name:
                    child_visible = True
                child.setHidden(not child_visible)
                if child_visible:
                    any_visible = True
            else:
                matches_text = not text or text in name
                matches_ext = not ext_filter or any(name.endswith(e) for e in ext_filter)
                visible = matches_text and matches_ext
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
        self._unpacker.browse("")

        # Trigger WWise audio event scan (CLI returns quickly if no WWiseAudio folder)
        self._wwise_root = ""
        self._wwise_events_prefix = ""
        self._wwise_audio_map = {}
        self._wwise_scan_done = False
        self._unpacker.scan_wwise_events()

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
            # Add placeholder for lazy loading
            placeholder = QTreeWidgetItem(["Loading..."])
            placeholder.setData(0, Qt.ItemDataRole.UserRole, _PLACEHOLDER)
            item.addChild(placeholder)
            parent.addChild(item)

        for entry in files:
            item = QTreeWidgetItem([entry["name"]])
            item.setData(0, Qt.ItemDataRole.UserRole, self._make_path(path, entry["name"]))
            item.setData(0, Qt.ItemDataRole.UserRole + 1, False)  # is_folder
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
                    parent.addChild(item)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    _IMAGE_EXTS = frozenset({".tga", ".png", ".dds", ".bmp", ".jpg", ".jpeg"})
    _AUDIO_EXTS = frozenset({".wav", ".ogg", ".wem", ".bnk", ".ewem"})

    def _show_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if item is None:
            return

        vfs_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
        if vfs_path == _PLACEHOLDER:
            return

        is_folder = item.data(0, Qt.ItemDataRole.UserRole + 1)
        audio_data = item.data(0, Qt.ItemDataRole.UserRole + 2)

        if is_folder:
            return

        suffix = Path(vfs_path.lower()).suffix

        menu = QMenu(self)

        if audio_data or suffix in self._AUDIO_EXTS:
            act = QAction("Preview Audio", self)
            if audio_data:
                act.triggered.connect(lambda checked=False, d=audio_data: self._try_audio_preview(d))
            else:
                act.triggered.connect(lambda checked=False, p=vfs_path: self._preview_audio_vfs(p))
            menu.addAction(act)
        elif suffix in self._IMAGE_EXTS:
            act = QAction("Preview Image", self)
            act.triggered.connect(lambda checked=False, p=vfs_path: self._preview_image_vfs(p))
            menu.addAction(act)
        else:
            act = QAction("View Properties", self)
            act.triggered.connect(lambda checked=False, p=vfs_path: self._preview_props_ctx(p))
            menu.addAction(act)

        if menu.actions():
            menu.exec(self._tree.viewport().mapToGlobal(pos))

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

    def _preview_audio_vfs(self, vfs_path: str):
        """Preview a VFS audio file — use local export if available, else temp-export."""
        local = self._find_local_file(vfs_path)
        if local:
            self.audio_preview.emit(str(local))
            return

        # Check temp dir for previously-previewed files
        if self._audio_preview_temp_dir:
            audio_format = config.get("export_audio_format") or "wav"
            temp_candidate = self._audio_preview_temp_dir / vfs_path.lstrip("/")
            temp_candidate = temp_candidate.with_suffix(f".{audio_format}")
            if temp_candidate.is_file():
                self.audio_preview.emit(str(temp_candidate))
                return

        # Export to temp dir for preview
        if self._exporting:
            self._status_label.setText("Export in progress — try again after it finishes")
            return
        if not self._mounted:
            self._status_label.setText("Mount an archive first")
            return
        if not self._audio_preview_temp_dir:
            self._status_label.setText("Audio not exported yet — export it first to preview")
            return

        temp_dir = str(self._audio_preview_temp_dir)
        audio_format = config.get("export_audio_format") or "wav"
        # Predict output path
        name = vfs_path.rsplit("/", 1)[-1]
        expected = Path(temp_dir) / vfs_path.lstrip("/")
        expected = expected.with_suffix(f".{audio_format}")
        self._pending_temp_preview = str(expected)

        self._status_label.setText(f"Exporting for preview: {name}")
        self._begin_export()
        self._unpacker.export([vfs_path], temp_dir,
                              formats={"mesh": False, "texture": False, "props": False,
                                       "animation": False, "audio": True},
                              audio_format=audio_format)

    def _preview_image_vfs(self, vfs_path: str):
        """Preview a VFS image file from the local export directory."""
        local = self._find_local_file(vfs_path)
        if local:
            self.tga_preview.emit(str(local))
        else:
            self._status_label.setText("Image not exported yet — export it first to preview")

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
            child.setToolTip(0, f"Type: {export_type}\nPackage: {path}")

        item.setExpanded(True)
        self._status_label.setText(f"{len(exports)} export(s) in {path}")

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
        self._pending_temp_preview = str(expected)

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

        # Handle temp audio preview export
        if self._pending_temp_preview:
            expected = self._pending_temp_preview
            self._pending_temp_preview = None
            self._exporting = False
            self._export_btn.setEnabled(True)
            self._export_folder_btn.setEnabled(True)
            self._cancel_btn.setEnabled(False)
            # Try to find the exported file (may have slightly different path)
            if Path(expected).is_file():
                self._status_label.setText("Ready")
                self.audio_preview.emit(expected)
            elif succeeded:
                # Use the first succeeded path as fallback
                fallback = Path(succeeded[0])
                if fallback.is_file():
                    self._status_label.setText("Ready")
                    self.audio_preview.emit(str(fallback))
                else:
                    self._status_label.setText("Preview export completed but file not found")
            else:
                self._status_label.setText("Preview export failed")
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

    @Slot()
    def _on_process_ended(self):
        self._exporting = False
        self._mounted = False
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
