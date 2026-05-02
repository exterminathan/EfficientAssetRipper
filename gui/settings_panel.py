"""Settings dialog for configuring paths, timeouts, presets, and appearance."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import config
from gui.color_schemes import (
    SCHEME_KEYS,
    SCHEMES,
    get_scheme,
    list_scheme_names,
    register_custom_scheme,
)
import gui.theme as theme


class PathPicker(QWidget):
    """A line-edit + browse button for picking files or folders."""

    changed = Signal(str)

    def __init__(self, mode: str = "folder", filter_str: str = "", parent=None):
        super().__init__(parent)
        self._mode = mode
        self._filter = filter_str

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.line_edit = QLineEdit()
        self.line_edit.textChanged.connect(self.changed.emit)
        layout.addWidget(self.line_edit)

        btn = QPushButton("Browse...")
        btn.setFixedWidth(80)
        btn.clicked.connect(self._browse)
        layout.addWidget(btn)

    def _browse(self):
        if self._mode == "folder":
            path = QFileDialog.getExistingDirectory(self, "Select Folder")
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Select File", "", self._filter
            )
        if path:
            self.line_edit.setText(path)

    def text(self) -> str:
        return self.line_edit.text()

    def setText(self, text: str):
        self.line_edit.setText(text)


class SettingsDialog(QDialog):
    """Application settings dialog."""

    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(650)
        self.setMinimumHeight(500)

        outer = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_widget = QWidget()
        layout = QVBoxLayout(scroll_widget)
        scroll.setWidget(scroll_widget)
        outer.addWidget(scroll, stretch=1)

        # --- Paths group ---
        paths_group = QGroupBox("Paths")
        paths_form = QFormLayout(paths_group)

        self.game_folder = PathPicker(mode="folder")
        self.game_folder.setText(config.get("game_folder"))
        paths_form.addRow("Game Folder:", self.game_folder)

        self.blender_exe = PathPicker(
            mode="file", filter_str="Blender (blender.exe);;All Files (*)"
        )
        self.blender_exe.setText(
            config.get("blender_exe") or self._auto_detect_blender()
        )
        paths_form.addRow("Blender Executable:", self.blender_exe)

        self.output_dir = PathPicker(mode="folder")
        self.output_dir.setText(config.get("output_dir"))
        paths_form.addRow("Output Directory:", self.output_dir)

        self.everything_dll = PathPicker(
            mode="file", filter_str="DLL Files (*.dll);;All Files (*)"
        )
        self.everything_dll.setText(
            config.get("everything_dll") or self._auto_detect_everything()
        )
        paths_form.addRow("Everything SDK DLL:", self.everything_dll)

        self.presets_path = PathPicker(
            mode="file", filter_str="JSON Files (*.json);;All Files (*)"
        )
        self.presets_path.setText(str(config.get_presets_path()))
        paths_form.addRow("Texture Presets JSON:", self.presets_path)

        layout.addWidget(paths_group)

        # --- Processing group ---
        proc_group = QGroupBox("Processing")
        proc_form = QFormLayout(proc_group)

        self.addon_name = QLineEdit(config.get("psk_addon_name"))
        proc_form.addRow("PSK Import Addon:", self.addon_name)

        self.timeout = QSpinBox()
        self.timeout.setRange(10, 3600)
        self.timeout.setSuffix(" seconds")
        self.timeout.setValue(config.get_int("timeout_seconds") or 120)
        proc_form.addRow("Timeout per Asset:", self.timeout)

        layout.addWidget(proc_group)

        # --- Unpacker / CUE4Parse group ---
        unpack_group = QGroupBox("Unpacker (CUE4ParseCLI)")
        unpack_form = QFormLayout(unpack_group)

        self.cue4parse_cli = PathPicker(
            mode="file", filter_str="Executable (CUE4ParseCLI.exe);;All Files (*)"
        )
        self.cue4parse_cli.setText(config.get("cue4parse_cli"))
        unpack_form.addRow("CUE4Parse CLI:", self.cue4parse_cli)

        self.unpack_output_dir = PathPicker(mode="folder")
        self.unpack_output_dir.setText(config.get("unpack_output_dir"))
        unpack_form.addRow("Unpack Output Dir:", self.unpack_output_dir)

        layout.addWidget(unpack_group)

        # --- Export Formats group ---
        fmt_group = QGroupBox("Export Formats")
        fmt_form = QFormLayout(fmt_group)

        self.texture_format = QComboBox()
        self.texture_format.addItems(["png", "tga"])
        self.texture_format.setCurrentText(config.get("export_texture_format"))
        fmt_form.addRow("Textures:", self.texture_format)

        self.audio_format = QComboBox()
        self.audio_format.addItems(["wav", "ogg"])
        self.audio_format.setCurrentText(config.get("export_audio_format"))
        fmt_form.addRow("Audio:", self.audio_format)

        mesh_label = QLabel("PSK / PSKX (automatic)")
        fmt_form.addRow("Meshes:", mesh_label)

        layout.addWidget(fmt_group)

        # --- Presets shortcut ---
        presets_btn = QPushButton("Open Texture Presets JSON in Editor")
        presets_btn.clicked.connect(self._open_presets)
        layout.addWidget(presets_btn)

        # --- Appearance / Color Scheme group ---
        appearance_group = QGroupBox("Appearance — Color Scheme")
        appearance_layout = QVBoxLayout(appearance_group)

        scheme_row = QHBoxLayout()
        scheme_row.addWidget(QLabel("Scheme:"))
        self._scheme_combo = QComboBox()
        self._scheme_combo.addItems(list_scheme_names())
        current = config.get("color_scheme") or theme.current_scheme_name()
        if current in [self._scheme_combo.itemText(i) for i in range(self._scheme_combo.count())]:
            self._scheme_combo.setCurrentText(current)
        self._scheme_combo.currentTextChanged.connect(self._on_scheme_changed)
        scheme_row.addWidget(self._scheme_combo)

        self._new_scheme_btn = QPushButton("New Custom…")
        self._new_scheme_btn.clicked.connect(self._new_custom_scheme)
        scheme_row.addWidget(self._new_scheme_btn)

        self._delete_scheme_btn = QPushButton("Delete Custom")
        self._delete_scheme_btn.clicked.connect(self._delete_custom_scheme)
        scheme_row.addWidget(self._delete_scheme_btn)

        scheme_row.addStretch()
        appearance_layout.addLayout(scheme_row)

        # Scrollable grid of color swatches for customisation
        self._color_scroll = QScrollArea()
        self._color_scroll.setWidgetResizable(True)
        self._color_scroll.setMaximumHeight(280)
        self._color_grid_widget = QWidget()
        self._color_grid = QGridLayout(self._color_grid_widget)
        self._color_grid.setContentsMargins(4, 4, 4, 4)
        self._color_grid.setSpacing(4)
        self._color_scroll.setWidget(self._color_grid_widget)
        appearance_layout.addWidget(self._color_scroll)

        self._swatch_buttons: dict[str, QPushButton] = {}  # token → button
        self._custom_colors: dict[str, str] = {}  # current overrides

        self._populate_color_grid()
        self._update_delete_button()

        layout.addWidget(appearance_group)

        # --- Test group ---
        test_group = QGroupBox("Verify Setup")
        test_layout = QVBoxLayout(test_group)

        test_btn = QPushButton("Test All Paths && SDKs")
        test_btn.clicked.connect(self._run_tests)
        test_layout.addWidget(test_btn)

        self._test_output = QTextEdit()
        self._test_output.setReadOnly(True)
        self._test_output.setMaximumHeight(200)

        test_layout.addWidget(self._test_output)

        layout.addWidget(test_group)

        # --- Buttons (outside scroll area, always visible) ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # Path-typed settings: keys checked for existence on save (folder vs file).
    _PATH_FIELDS_FOLDER = ("game_folder", "output_dir", "unpack_output_dir")
    _PATH_FIELDS_FILE = ("blender_exe", "everything_dll", "cue4parse_cli")

    def _confirm_missing_path(self, label: str, value: str) -> bool:
        """Prompt the user when a path is set but doesn't exist on disk."""
        reply = QMessageBox.question(
            self,
            "Path doesn't exist",
            f"{label}:\n{value}\n\nThis path doesn't exist. Save anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _validate_paths(self) -> bool:
        """Return False if the user cancels at any non-existent path prompt."""
        checks: list[tuple[str, str, str]] = [  # (label, value, kind)
            ("Game Folder", self.game_folder.text().strip(), "folder"),
            ("Output Directory", self.output_dir.text().strip(), "folder"),
            ("Unpack Output Dir", self.unpack_output_dir.text().strip(), "folder"),
            ("Blender Executable", self.blender_exe.text().strip(), "file"),
            ("Everything SDK DLL", self.everything_dll.text().strip(), "file"),
            ("CUE4Parse CLI", self.cue4parse_cli.text().strip(), "file"),
        ]
        for label, value, kind in checks:
            if not value:
                continue
            ok = os.path.isdir(value) if kind == "folder" else os.path.isfile(value)
            if ok:
                continue
            if not self._confirm_missing_path(label, value):
                return False
        return True

    def _save(self):
        # Clamp the timeout into a sane range (the QSpinBox already constrains
        # via setRange, but be defensive in case the constraint changes).
        self.timeout.setValue(max(10, min(self.timeout.value(), 3600)))

        if not self._validate_paths():
            return

        config.set("game_folder", self.game_folder.text())
        config.set("blender_exe", self.blender_exe.text())
        config.set("output_dir", self.output_dir.text())
        config.set("everything_dll", self.everything_dll.text())
        # Presets path: a non-bundled location is allowed but requires a
        # one-time confirmation so a stray file substitution can't sneak in
        # during a settings round-trip.
        new_presets = self.presets_path.text().strip()
        if new_presets and not config.is_presets_path_safe(new_presets):
            already_confirmed = (
                config.get("presets_path_confirmed_external") == new_presets
            )
            if not already_confirmed:
                resp = QMessageBox.warning(
                    self,
                    "External texture_presets.json",
                    (
                        f"The selected texture_presets.json is outside the install dir:\n"
                        f"{new_presets}\n\n"
                        "Continue using this file? Click No to revert to the bundled defaults."
                    ),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if resp != QMessageBox.StandardButton.Yes:
                    new_presets = str(config.base_dir() / "data" / "texture_presets.json")
                    self.presets_path.setText(new_presets)
                else:
                    config.set("presets_path_confirmed_external", new_presets)
        config.set("presets_path", new_presets)
        config.set("psk_addon_name", self.addon_name.text())
        config.set("timeout_seconds", self.timeout.value())
        config.set("cue4parse_cli", self.cue4parse_cli.text())
        config.set("unpack_output_dir", self.unpack_output_dir.text())
        config.set("export_texture_format", self.texture_format.currentText())
        config.set("export_audio_format", self.audio_format.currentText())

        # Save colour scheme choice
        scheme_name = self._scheme_combo.currentText()
        config.set("color_scheme", scheme_name)

        # Persist any custom overrides for the currently selected scheme
        if self._custom_colors:
            self._persist_custom_scheme(scheme_name, self._custom_colors)

        # Apply scheme live
        app = QApplication.instance()
        if app:
            theme.apply(app, scheme_name)

        self.settings_changed.emit()
        self.accept()

    # ------------------------------------------------------------------
    # Colour-scheme helpers
    # ------------------------------------------------------------------

    # Built-in schemes that cannot be deleted or have colours reassigned
    _BUILTIN = {"Dusk", "Bloom", "Slate", "Midnight"}

    def _on_scheme_changed(self, name: str):
        """When the user picks a different scheme in the dropdown."""
        self._custom_colors = dict(get_scheme(name))
        self._populate_color_grid()
        self._update_delete_button()

    def _populate_color_grid(self):
        """Fill the colour swatch grid from the currently selected scheme."""
        # Clear existing widgets
        while self._color_grid.count():
            item = self._color_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._swatch_buttons.clear()

        scheme_name = self._scheme_combo.currentText()
        colors = dict(get_scheme(scheme_name))
        if self._custom_colors:
            colors.update(self._custom_colors)
        else:
            self._custom_colors = dict(colors)

        editable = scheme_name not in self._BUILTIN

        cols = 4
        for idx, key in enumerate(SCHEME_KEYS):
            row, col = divmod(idx, cols)
            container = QHBoxLayout()
            lbl = QLabel(key.replace("_", " ").title())
            lbl.setFixedWidth(120)
            container.addWidget(lbl)

            btn = QPushButton()
            btn.setFixedSize(36, 22)
            hex_color = colors.get(key, "#888888")
            btn.setStyleSheet(
                f"background-color: {hex_color}; border: 1px solid #666; border-radius: 3px;"
            )
            btn.setToolTip(hex_color)
            if editable:
                btn.clicked.connect(lambda checked=False, k=key: self._pick_color(k))
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                btn.setEnabled(False)
            self._swatch_buttons[key] = btn
            container.addWidget(btn)

            wrapper = QWidget()
            wrapper.setLayout(container)
            self._color_grid.addWidget(wrapper, row, col)

    def _pick_color(self, key: str):
        """Open a QColorDialog for a specific token."""
        current = QColor(self._custom_colors.get(key, "#888888"))
        color = QColorDialog.getColor(current, self, f"Pick colour for {key}")
        if color.isValid():
            hex_val = color.name()
            self._custom_colors[key] = hex_val
            btn = self._swatch_buttons.get(key)
            if btn:
                btn.setStyleSheet(
                    f"background-color: {hex_val}; border: 1px solid #666; border-radius: 3px;"
                )
                btn.setToolTip(hex_val)

    def _new_custom_scheme(self):
        """Create a new custom scheme by copying the currently selected one."""
        name, ok = QInputDialog.getText(
            self, "New Custom Scheme", "Scheme name:",
        )
        if not ok or not name:
            return
        name = name.strip()
        if name in SCHEMES:
            QMessageBox.warning(self, "Duplicate", f"A scheme named '{name}' already exists.")
            return

        # Copy the currently viewed colours
        base = dict(get_scheme(self._scheme_combo.currentText()))
        register_custom_scheme(name, base)
        self._save_custom_schemes_to_config()

        # Refresh combo
        self._scheme_combo.blockSignals(True)
        self._scheme_combo.clear()
        self._scheme_combo.addItems(list_scheme_names())
        self._scheme_combo.setCurrentText(name)
        self._scheme_combo.blockSignals(False)

        self._custom_colors = dict(base)
        self._populate_color_grid()
        self._update_delete_button()

    def _delete_custom_scheme(self):
        """Delete the currently selected custom scheme."""
        name = self._scheme_combo.currentText()
        if name in self._BUILTIN:
            return
        reply = QMessageBox.question(
            self, "Delete Scheme",
            f"Delete custom scheme '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        SCHEMES.pop(name, None)
        self._save_custom_schemes_to_config()

        self._scheme_combo.blockSignals(True)
        self._scheme_combo.clear()
        self._scheme_combo.addItems(list_scheme_names())
        self._scheme_combo.setCurrentText("Dusk")
        self._scheme_combo.blockSignals(False)

        self._on_scheme_changed("Dusk")

    def _update_delete_button(self):
        name = self._scheme_combo.currentText()
        self._delete_scheme_btn.setEnabled(name not in self._BUILTIN)

    def _persist_custom_scheme(self, name: str, colors: dict[str, str]):
        """Register and save a custom scheme's colours."""
        if name in self._BUILTIN:
            return
        register_custom_scheme(name, colors)
        self._save_custom_schemes_to_config()

    def _save_custom_schemes_to_config(self):
        """Persist all non-built-in schemes to config."""
        custom = {}
        for sname, scolors in SCHEMES.items():
            if sname not in self._BUILTIN:
                custom[sname] = scolors
        config.set("custom_schemes", json.dumps(custom))

    def _open_presets(self):
        path = self.presets_path.text().strip()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(
                self, "Presets file not found",
                f"Could not open texture presets:\n{path or '(no path set)'}",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _run_tests(self):
        """Test all configured paths and SDK connections."""
        self._test_output.clear()
        results: list[tuple[str, bool, str]] = []  # (test_name, passed, detail)

        # 1. Game folder
        gf = self.game_folder.text()
        if gf and os.path.isdir(gf):
            results.append(("Game Folder", True, gf))
        elif gf:
            results.append(("Game Folder", False, f"Directory not found: {gf}"))
        else:
            results.append(("Game Folder", False, "Not set"))

        # 2. Blender exe
        be = self.blender_exe.text()
        if be and os.path.isfile(be):
            # Try running blender --version
            import subprocess
            try:
                proc = subprocess.run(
                    [be, "--version"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                ver_line = proc.stdout.strip().splitlines()[0] if proc.stdout else "Unknown"
                results.append(("Blender", True, ver_line))
            except Exception as e:
                results.append(("Blender", False, f"Exe exists but failed to run: {e}"))
        elif be:
            results.append(("Blender", False, f"File not found: {be}"))
        else:
            results.append(("Blender", False, "Not set"))

        # 3. Output dir
        od = self.output_dir.text()
        if od and os.path.isdir(od):
            results.append(("Output Directory", True, od))
        elif od:
            # Try to create it
            try:
                os.makedirs(od, exist_ok=True)
                results.append(("Output Directory", True, f"Created: {od}"))
            except Exception as e:
                results.append(("Output Directory", False, f"Cannot create: {e}"))
        else:
            results.append(("Output Directory", False, "Not set"))

        # 4. Everything SDK
        dll_path = self.everything_dll.text()
        if dll_path and os.path.isfile(dll_path):
            results.append(("Everything DLL", True, f"Found: {dll_path}"))
            try:
                from core.everything import EverythingSDK, reset_sdk
                reset_sdk()
                sdk = EverythingSDK(dll_path)

                # Test IPC connection
                ok, msg = sdk.test_connection()
                results.append(("Everything IPC", ok, msg))

                # Test folder search if game folder is set
                if ok and gf and os.path.isdir(gf):
                    count, msg = sdk.test_folder_search(gf)
                    results.append(("Folder Search", count > 0, msg))

                    # Try finding PSK files
                    psk_files = sdk.find_psk_files(folder=gf)
                    if psk_files:
                        results.append((
                            "PSK Search",
                            True,
                            f"Found {len(psk_files)} PSK/PSKX files, e.g.: {psk_files[0].name}",
                        ))
                    else:
                        results.append(("PSK Search", False, f"No PSK/PSKX files found under: {gf}"))
            except Exception as e:
                results.append(("Everything SDK", False, f"Error: {e}"))
        elif dll_path:
            results.append(("Everything DLL", False, f"File not found: {dll_path}"))
        else:
            results.append(("Everything DLL", False, "Not set"))

        # 5. Presets JSON
        pp = self.presets_path.text()
        if pp and os.path.isfile(pp):
            import json
            try:
                with open(pp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                preset_names = list(data.get("presets", {}).keys())
                results.append(("Presets JSON", True, f"Presets: {', '.join(preset_names)}"))
            except Exception as e:
                results.append(("Presets JSON", False, f"Parse error: {e}"))
        elif pp:
            results.append(("Presets JSON", False, f"File not found: {pp}"))
        else:
            results.append(("Presets JSON", False, "Not set"))

        # 6. CUE4Parse CLI
        cli_path = self.cue4parse_cli.text()
        if cli_path and os.path.isfile(cli_path):
            import subprocess
            try:
                proc = subprocess.run(
                    [cli_path, "--version"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                ver_line = proc.stdout.strip().splitlines()[0] if proc.stdout else "Unknown"
                results.append(("CUE4Parse CLI", True, ver_line))
            except Exception as e:
                results.append(("CUE4Parse CLI", False, f"Exe exists but failed to run: {e}"))
        elif cli_path:
            results.append(("CUE4Parse CLI", False, f"File not found: {cli_path}"))
        else:
            results.append(("CUE4Parse CLI", False, "Not set (optional)"))

        # Render results
        lines: list[str] = []
        all_pass = True
        for name, passed, detail in results:
            icon = "PASS" if passed else "FAIL"
            if not passed:
                all_pass = False
            lines.append(f"[{icon}] {name}: {detail}")

        lines.append("")
        if all_pass:
            lines.append("All tests passed!")
        else:
            lines.append("Some tests failed — check paths above.")

        self._test_output.setPlainText("\n".join(lines))

    @staticmethod
    def _auto_detect_blender() -> str:
        candidates = [
            r"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return ""

    @staticmethod
    def _auto_detect_everything() -> str:
        candidates = [
            r"C:\Program Files\Everything\Everything64.dll",
            r"C:\Program Files (x86)\Everything\Everything64.dll",
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return ""
