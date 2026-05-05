"""Main application window — ties together all panels."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QRunnable, QThread, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import config
from _version import __version__
from core.asset_scanner import (
    AssetEntry,
    AssetScanner,
    load_scan_cache,
    save_scan_cache,
    sweep_old_cache_backups,
)
from core.everything import EverythingError, get_sdk, reset_sdk
from core.job_manager import JobManager
from core.profile_manager import ProfileLoadError, ProfileManager
from gui.asset_browser import AssetBrowser
from gui.blend_combiner import BlendCombinerPanel
from gui.tga_previewer import TGAPreviewerPanel
from gui.audio_previewer import AudioPreviewerPanel
from gui.mesh_previewer import MeshPreviewerPanel
from gui.log_viewer import LogViewer
from gui.profile_bar import ProfileBar
from gui.psk_picker import PskPickerPanel
from gui.queue_panel import QueuePanel
from gui.text_viewer import TextViewer
from gui.unpacker_panel import UnpackerPanel
from gui.settings_panel import SettingsDialog
import gui.theme as theme

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scanner worker (runs in background thread)
# ---------------------------------------------------------------------------

class ScanWorker(QThread):
    """Background thread for asset scanning."""

    progress = Signal(int, int, str)
    finished = Signal(list)         # list[AssetEntry]
    error = Signal(str)

    def __init__(self, scanner: AssetScanner, parent=None):
        super().__init__(parent)
        self._scanner = scanner

    def cancel(self):
        self._scanner.cancel()
        self.requestInterruption()

    def run(self):
        try:
            results = self._scanner.scan(
                progress_callback=lambda cur, tot, msg: self.progress.emit(cur, tot, msg)
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class RescanWorker(QThread):
    """Background thread for re-resolving individual assets."""

    progress = Signal(int, int, str)
    finished = Signal(int, int)     # (resolved_count, still_incomplete)
    error = Signal(str)

    def __init__(self, scanner: AssetScanner, entries: list, parent=None):
        super().__init__(parent)
        self._scanner = scanner
        self._entries = entries
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        self._scanner.cancel()
        self.requestInterruption()

    def run(self):
        try:
            total = len(self._entries)
            resolved = 0
            for idx, entry in enumerate(self._entries):
                if self._cancelled or self.isInterruptionRequested():
                    self.progress.emit(idx, total, "Re-scan cancelled")
                    break
                self.progress.emit(idx, total, f"Re-scanning: {entry.name}")
                self._scanner.resolve_entry(entry)
                if entry.status == "ready":
                    resolved += 1
            self.progress.emit(total, total, "Re-scan complete")
            still_incomplete = total - resolved
            self.finished.emit(resolved, still_incomplete)
        except Exception as e:
            self.error.emit(str(e))


class _PickerResolveWorker(QThread):
    """Background thread for resolving PSK entries from the picker."""

    progress = Signal(int, int, str)
    entry_resolved = Signal(object)  # emits each AssetEntry immediately after resolution
    finished = Signal(int, int)     # (resolved_count, still_incomplete)
    error = Signal(str)

    def __init__(self, scanner: AssetScanner, entries: list, parent=None):
        super().__init__(parent)
        self._scanner = scanner
        self._entries = entries
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        self._scanner.cancel()
        self.requestInterruption()

    def run(self):
        try:
            total = len(self._entries)
            resolved = 0
            for idx, entry in enumerate(self._entries):
                if self._cancelled or self.isInterruptionRequested():
                    self.progress.emit(idx, total, "Resolve cancelled")
                    break
                self.progress.emit(idx, total, f"Resolving: {entry.name}")
                self._scanner.resolve_entry(entry)
                if entry.status == "ready":
                    resolved += 1
                self.entry_resolved.emit(entry)
            self.progress.emit(total, total, "Resolve complete")
            still_incomplete = total - resolved
            self.finished.emit(resolved, still_incomplete)
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Cache write runnable — moves disk I/O off the GUI thread
# ---------------------------------------------------------------------------

class _CacheWriteRunnable(QRunnable):
    """Submits save_scan_cache work to QThreadPool."""

    def __init__(self, assets: list, game_folder: str, on_done=None):
        super().__init__()
        self._assets = list(assets)
        self._game_folder = game_folder
        self._on_done = on_done

    def run(self):
        try:
            save_scan_cache(self._assets, self._game_folder)
        except Exception:
            log.exception("Async cache write failed")
        finally:
            if self._on_done is not None:
                try:
                    self._on_done()
                except Exception:
                    log.exception("Cache write completion callback failed")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"EfficientAssetRipper v{__version__}")
        self.setMinimumSize(1200, 700)

        self._job_manager: JobManager | None = None
        self._current_profile_name = ""
        # All long-running QThread workers register here on creation and
        # auto-prune themselves on `finished`. closeEvent walks the list to
        # make sure nothing keeps spinning past window destruction.
        self._active_workers: list[QThread] = []
        # Outstanding cache writes — closeEvent waits on this counter.
        self._pending_cache_writes = 0

        # Profile manager
        self._profile_manager = ProfileManager()
        self._profile_manager.migrate_from_qsettings(config)

        self._build_ui()
        self._build_menu()
        self._refresh_blender_dependent_ui()
        self._load_initial_profile()
        # Defer the setup wizard until after splash → window handoff so the
        # modal dialog doesn't fight the splash for focus.
        QTimer.singleShot(0, self._maybe_run_setup_wizard)
        # Same deferral for the resume prompt — keeps focus order sane and
        # gives the wizard precedence on first launch.
        QTimer.singleShot(0, self._maybe_prompt_resume)

        # Best-effort startup housekeeping. Old scan-cache backups left behind
        # by version-bump renames pile up otherwise.
        try:
            sweep_old_cache_backups()
        except Exception:
            log.exception("sweep_old_cache_backups failed (non-fatal)")

        # Kick off the auto-update check 2s after launch so it never delays
        # the splash. UpdateChecker fails silently on any error.
        self._update_info = None
        self._update_checker = None
        self._manual_update_checker = None
        QTimer.singleShot(2000, self._start_update_check)

    # ------------------------------------------------------------------
    # Worker tracking
    # ------------------------------------------------------------------

    def _track_worker(self, worker: QThread) -> None:
        """Register a worker thread for shutdown tracking + auto-cleanup."""
        self._active_workers.append(worker)
        worker.finished.connect(lambda w=worker: self._on_worker_finished(w))
        worker.finished.connect(worker.deleteLater)

    def _on_worker_finished(self, worker: QThread) -> None:
        try:
            self._active_workers.remove(worker)
        except ValueError:
            pass

    def _maybe_run_setup_wizard(self):
        """Show the first-run wizard if it hasn't been completed yet."""
        from gui.setup_wizard import SetupWizard, should_show_setup
        if not should_show_setup():
            return
        wizard = SetupWizard(self)
        wizard.exec()

    def _force_run_setup_wizard(self):
        """Help-menu trigger: re-arm and re-fire the wizard regardless of state."""
        from gui.setup_wizard import SetupWizard
        config.set("setup_complete", "")
        wizard = SetupWizard(self)
        wizard.exec()

    def _start_update_check(self):
        from core.update_check import UpdateChecker
        self._update_checker = UpdateChecker(parent=self)
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.start()

    @Slot(object)
    def _on_update_available(self, info):
        self._update_info = info
        self._statusbar.showMessage(
            f"Update available: {info.latest} (you have v{info.current}) — Help → About",
            10_000,
        )
        log.info("Update available: %s (current %s)", info.latest, info.current)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Profile bar (top) ─────────────────────────────────────────
        self._profile_bar = ProfileBar(
            self._profile_manager,
            busy_check=self._is_busy,
            cancel_fn=self._cancel_active_ops,
            parent=self,
        )
        self._profile_bar.profile_switch_requested.connect(self._switch_profile)
        self._profile_bar.manage_requested.connect(self._open_profile_dialog)
        main_layout.addWidget(self._profile_bar)

        # ── Main content area ─────────────────────────────────────────
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)

        # Left panel: tabs for asset browser and PSK picker
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        scan_bar = QHBoxLayout()
        self._scan_btn = QPushButton("Scan Game Folder")
        self._scan_btn.clicked.connect(self._start_scan)
        scan_bar.addWidget(self._scan_btn)

        self._cancel_scan_btn = QPushButton("Cancel Scan")
        self._cancel_scan_btn.setEnabled(False)
        self._cancel_scan_btn.clicked.connect(self._cancel_scan)
        scan_bar.addWidget(self._cancel_scan_btn)

        scan_bar.addStretch()
        left_layout.addLayout(scan_bar)

        self._left_tabs = QTabWidget()

        self._browser = AssetBrowser()
        self._left_tabs.addTab(self._browser, "Asset Browser")

        self._psk_picker = PskPickerPanel()
        self._left_tabs.addTab(self._psk_picker, "PSK Picker")

        self._unpacker_panel = UnpackerPanel()
        self._left_tabs.addTab(self._unpacker_panel, "Unpacker")

        left_layout.addWidget(self._left_tabs)

        # Right panel: tabs (queue/log vs combiner)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._right_tabs = QTabWidget()

        # Tab 1: Queue + Log
        queue_log_widget = QWidget()
        ql_layout = QVBoxLayout(queue_log_widget)
        ql_layout.setContentsMargins(0, 0, 0, 0)

        right_splitter = QSplitter(Qt.Orientation.Vertical)

        self._queue = QueuePanel()
        right_splitter.addWidget(self._queue)

        self._log = LogViewer()
        right_splitter.addWidget(self._log)

        right_splitter.setSizes([300, 200])
        ql_layout.addWidget(right_splitter)

        self._right_tabs.addTab(queue_log_widget, "Queue / Log")

        # Tab 2: Blend Combiner
        self._combiner = BlendCombinerPanel()
        self._combiner.log_message.connect(self._log.append)
        self._right_tabs.addTab(self._combiner, "Blend Combiner")

        # Tab 3: TGA Previewer
        self._tga_previewer = TGAPreviewerPanel()
        self._right_tabs.addTab(self._tga_previewer, "TGA Previewer")

        # Tab 4: Text Viewer
        self._text_viewer = TextViewer()
        self._right_tabs.addTab(self._text_viewer, "Text Viewer")

        # Tab 5: Audio Preview
        self._audio_previewer = AudioPreviewerPanel()
        self._right_tabs.addTab(self._audio_previewer, "Audio Preview")

        # Tab 6: Mesh Preview
        self._mesh_previewer = MeshPreviewerPanel()
        self._right_tabs.addTab(self._mesh_previewer, "Mesh Preview")

        right_layout.addWidget(self._right_tabs)

        # Main horizontal splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([600, 600])
        content_layout.addWidget(splitter)

        main_layout.addWidget(content, stretch=1)

        # Status bar
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("Ready — configure settings and scan to begin")

        # Connect signals
        self._queue.process_requested.connect(self._process_queue)
        self._queue.cancel_requested.connect(self._cancel_processing)
        self._queue.reprocess_requested.connect(self._reprocess_asset)
        self._browser.rescan_requested.connect(self._rescan_selected)
        self._browser.add_to_queue_requested.connect(self._add_browser_to_queue)
        self._browser.reprocess_requested.connect(self._reprocess_asset)
        self._browser.delete_requested.connect(self._on_browser_delete)
        self._browser.mesh_preview_requested.connect(self._on_browser_mesh_preview)
        self._browser.props_view_requested.connect(self._on_browser_props_view)
        self._psk_picker.add_to_queue_requested.connect(self._add_picker_to_queue)
        self._unpacker_panel.psk_extracted.connect(self._on_psks_extracted)
        self._unpacker_panel.log_message.connect(self._log.append)
        self._unpacker_panel.version_mismatch.connect(self._log.show_alert)
        self._unpacker_panel.props_viewed.connect(self._show_in_text_viewer)
        self._unpacker_panel.audio_preview.connect(self._on_audio_preview)
        self._unpacker_panel.tga_preview.connect(self._on_tga_preview)
        self._unpacker_panel.mesh_preview.connect(self._on_mesh_preview)

        # Give unpacker panel access to the audio previewer's temp directory
        self._unpacker_panel._audio_preview_temp_dir = self._audio_previewer.temp_dir

    def _show_in_text_viewer(self, title: str, text: str):
        """Display text content in the Text Viewer tab and switch to it."""
        self._text_viewer.show_text(title, text)
        self._right_tabs.setCurrentWidget(self._text_viewer)

    def _on_audio_preview(self, path: str):
        """Load audio file in the Audio Preview tab and switch to it."""
        self._audio_previewer.load_file(path)
        self._right_tabs.setCurrentWidget(self._audio_previewer)

    def _on_tga_preview(self, path: str):
        """Load image file in the TGA Previewer tab and switch to it."""
        self._tga_previewer.load_file(path)
        self._right_tabs.setCurrentWidget(self._tga_previewer)

    def _on_mesh_preview(self, path: str):
        """Load .psk in the Mesh Preview tab and switch to it."""
        self._mesh_previewer.load_psk(path)
        self._right_tabs.setCurrentWidget(self._mesh_previewer)

    def _on_browser_mesh_preview(self, asset):
        self._on_mesh_preview(str(asset.psk_path))

    def _on_browser_props_view(self, asset):
        """Open the asset's .props.txt in the Text Viewer; toast if absent."""
        props = asset.psk_path.with_suffix(".props.txt")
        if not props.is_file():
            self._statusbar.showMessage(f"No .props.txt for {asset.name}", 4000)
            return
        try:
            text = props.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            self._statusbar.showMessage(f"Could not read props: {e}", 5000)
            return
        self._show_in_text_viewer(f"Properties — {asset.name}", text)

    def _build_menu(self):
        from PySide6.QtGui import QAction

        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        settings_action = file_menu.addAction("&Settings...", self._open_settings)
        # NoRole keeps Qt from auto-promoting "Settings"/"Exit" into the
        # platform-native application menu (or doubling the entry on
        # platforms where it inserts a duplicated mirror).
        settings_action.setMenuRole(QAction.MenuRole.NoRole)
        file_menu.addSeparator()
        exit_action = file_menu.addAction("E&xit", self.close)
        exit_action.setMenuRole(QAction.MenuRole.NoRole)

        tools_menu = menubar.addMenu("&Tools")
        self._blend_combiner_action = tools_menu.addAction(
            "&Blend Combiner",
            lambda: self._right_tabs.setCurrentWidget(self._combiner),
        )

        help_menu = menubar.addMenu("&Help")
        help_menu.addAction("Run Setup &Wizard...", self._force_run_setup_wizard)
        help_menu.addAction("Check for &Updates...", self._check_for_updates_now)
        help_menu.addSeparator()
        help_menu.addAction("&About...", self._show_about)

        # ── Window menu — toggle visibility of each tab ───────────────
        window_menu = menubar.addMenu("&Window")
        self._tab_actions = {}
        for tabs, names in [
            (self._left_tabs, ["Asset Browser", "PSK Picker", "Unpacker"]),
            (self._right_tabs, ["Queue / Log", "Blend Combiner", "TGA Previewer", "Text Viewer", "Audio Preview"]),
        ]:
            for i, name in enumerate(names):
                action = window_menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(True)
                widget = tabs.widget(i)
                action.triggered.connect(
                    lambda checked, t=tabs, w=widget, n=name: self._toggle_tab(t, w, n, checked)
                )
                self._tab_actions[(id(tabs), name)] = (action, tabs, widget, i)

    def _show_about(self):
        from html import escape
        from urllib.parse import urlparse

        update_html = ""
        if self._update_info and self._update_info.is_newer:
            url = self._update_info.release_url or ""
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            # The update_check module already filters URLs, but defence-in-depth
            # belongs at the render site too — never embed a URL from anywhere
            # other than github.com over HTTPS.
            safe_url = url if (parsed.scheme == "https" and host == "github.com") else ""
            tag_html = escape(self._update_info.latest)
            if safe_url:
                update_html = (
                    f"<p><b>Update available:</b> {tag_html} — "
                    f'<a href="{escape(safe_url, quote=True)}">view release</a></p>'
                )
            else:
                update_html = f"<p><b>Update available:</b> {tag_html}</p>"
        QMessageBox.about(
            self,
            "About EfficientAssetRipper",
            f"<h3>EfficientAssetRipper v{__version__}</h3>"
            "<p>Unpack any UE4/5 game files and easily export them to "
            "Blender. Includes utility tools for previewing textures, "
            "combining models, and other useful features.</p>"
            "<p>Released under the MIT License.</p>"
            f"{update_html}"
            '<p><a href="https://github.com/exterminathan/EfficientAssetRipper">'
            "github.com/exterminathan/EfficientAssetRipper</a></p>",
        )

    def _check_for_updates_now(self):
        """Help-menu trigger: force a fresh update check and show the result."""
        from html import escape
        from urllib.parse import urlparse

        from core.update_check import UpdateChecker

        # If a previous menu-triggered checker is still running, ignore the
        # click — the user can wait for it to complete.
        if getattr(self, "_manual_update_checker", None) is not None:
            checker = self._manual_update_checker
            worker = getattr(checker, "_worker", None)
            if worker is not None and worker.isRunning():
                return

        progress = QProgressDialog("Checking for updates...", None, 0, 0, self)
        progress.setWindowTitle("Check for Updates")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.show()

        checker = UpdateChecker(parent=self)
        self._manual_update_checker = checker

        def _on_complete(info):
            try:
                progress.close()
            except Exception:  # noqa: BLE001
                pass
            self._manual_update_checker = None

            if info is None:
                QMessageBox.warning(
                    self, "Check for Updates",
                    "Could not reach the update server.\n\n"
                    "Check your network connection and try again later.",
                )
                return

            # Cache the freshest result so the About dialog reflects it too.
            self._update_info = info

            if info.is_newer:
                url = info.release_url or ""
                parsed = urlparse(url)
                host = (parsed.hostname or "").lower()
                safe_url = url if (parsed.scheme == "https" and host == "github.com") else ""
                tag_html = escape(info.latest)
                body = (
                    f"<p><b>A new version is available: {tag_html}</b></p>"
                    f"<p>You are running v{escape(info.current)}.</p>"
                )
                if safe_url:
                    body += (
                        f'<p><a href="{escape(safe_url, quote=True)}">'
                        "View the release on GitHub</a></p>"
                    )
                QMessageBox.information(self, "Update Available", body)
            else:
                QMessageBox.information(
                    self, "Up to Date",
                    f"You are running the latest version (v{info.current}).",
                )

        checker.check_complete.connect(_on_complete)
        checker.start(force_refresh=True)

    def _toggle_tab(self, tab_widget: QTabWidget, widget: QWidget, name: str, visible: bool):
        """Show or hide a tab in a QTabWidget."""
        idx = tab_widget.indexOf(widget)
        if visible and idx == -1:
            # Re-insert at the original position
            _, _, _, orig_idx = self._tab_actions[(id(tab_widget), name)]
            insert_at = min(orig_idx, tab_widget.count())
            tab_widget.insertTab(insert_at, widget, name)
        elif not visible and idx != -1:
            tab_widget.removeTab(idx)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _open_settings(self):
        dlg = SettingsDialog(self)
        dlg.settings_changed.connect(self._on_settings_changed)
        dlg.exec()

    def _on_settings_changed(self):
        reset_sdk()
        # Re-evaluate Blender availability — the user may have just set the
        # path or pointed it at a missing binary.
        from core.blender_runner import reset_blender_validation_cache
        reset_blender_validation_cache()
        self._refresh_blender_dependent_ui()
        self._log.append("Settings updated", "info")

    def _refresh_blender_dependent_ui(self):
        """Enable/disable Blender-touching UI based on ``is_blender_available()``.

        Called once on startup and whenever settings change. Keeps the Blend
        Combiner tab visible-but-greyed when Blender is missing so the user
        sees the feature exists; the Process Queue button is hard-disabled
        and the status bar carries a persistent reminder.
        """
        from core.blender_runner import is_blender_available
        available = is_blender_available()

        combiner_idx = self._right_tabs.indexOf(self._combiner)
        if combiner_idx != -1:
            self._right_tabs.setTabEnabled(combiner_idx, available)
            self._right_tabs.setTabText(
                combiner_idx,
                "Blend Combiner" if available else "Blend Combiner (no Blender)",
            )

        if hasattr(self, "_blend_combiner_action") and self._blend_combiner_action is not None:
            self._blend_combiner_action.setEnabled(available)

        if available:
            tip = ""
        else:
            tip = "Blender not configured — set blender_exe in Settings."
        self._queue.set_processing_enabled(available, tooltip=tip)

        if not available:
            self._statusbar.showMessage(
                "Blender not found — processing & Blend Combiner disabled. "
                "Set the path in Settings."
            )

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _start_scan(self):
        game_folder = config.get("game_folder")
        if not game_folder:
            QMessageBox.warning(
                self, "No Game Folder",
                "Set the game folder under Manage Profiles first."
            )
            return

        dll_path = config.get("everything_dll") or None

        try:
            sdk = get_sdk(dll_path)
        except EverythingError as e:
            QMessageBox.critical(
                self, "Everything SDK Error",
                f"Could not initialize Everything SDK:\n{e}\n\n"
                "Make sure Everything is running and the DLL path is correct."
            )
            return

        presets = config.load_presets()
        scanner = AssetScanner(game_folder, presets, sdk)

        # Seed scanner with any already-loaded cached entries so it skips them
        current_assets = self._browser.get_assets()
        if current_assets:
            scanner.seed_cache(current_assets)

        self._scan_btn.setEnabled(False)
        self._cancel_scan_btn.setEnabled(True)
        self._statusbar.showMessage("Scanning...")
        self._log.append(f"Scanning: {game_folder}", "info")

        self._scan_worker = ScanWorker(scanner, self)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.error.connect(self._on_scan_error)
        self._track_worker(self._scan_worker)
        self._scan_worker.start()

    @Slot(int, int, str)
    def _on_scan_progress(self, current: int, total: int, message: str):
        self._statusbar.showMessage(f"Scanning: {message} ({current}/{total})")
        self._queue.update_resolve_progress(current, total, message)

    def _cancel_scan(self):
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.cancel()
            self._cancel_scan_btn.setEnabled(False)
            self._log.append("Cancelling scan...", "warning")
            self._statusbar.showMessage("Cancelling scan...")

    @Slot(list)
    def _on_scan_finished(self, assets: list):
        self._scan_btn.setEnabled(True)
        self._cancel_scan_btn.setEnabled(False)
        self._browser.set_assets(assets)
        ready = sum(1 for a in assets if a.status == "ready")
        count_msg = f"{len(assets)} assets found, {ready} ready"
        self._log.append(f"Scan finished: {count_msg}", "success")
        self._statusbar.showMessage(f"Scan finished: {count_msg}")
        # Seed picker with already-processed paths
        processed = [a.psk_path for a in assets if a.processed]
        if processed:
            self._psk_picker.mark_processed(processed)

        # Save cache asynchronously
        game_folder = config.get("game_folder")
        if game_folder and assets:
            self._save_cache_async(
                assets, game_folder,
                on_done_msg=f"Scan cached ({len(assets)} assets) — will auto-load next time",
            )

    @Slot(str)
    def _on_scan_error(self, error: str):
        self._scan_btn.setEnabled(True)
        self._cancel_scan_btn.setEnabled(False)
        self._log.append(f"Scan failed: {error}", "error")
        self._statusbar.showMessage("Scan failed")
        QMessageBox.critical(self, "Scan Error", error)

    # ------------------------------------------------------------------
    # Profile system
    # ------------------------------------------------------------------

    def _load_initial_profile(self):
        """On startup, load the last-used profile (or first available)."""
        profiles = self._profile_manager.list_profiles()
        if not profiles:
            self._profile_manager.create_profile("Default")
            profiles = ["Default"]

        saved = config.get("active_profile") or ""
        if saved in profiles:
            target = saved
        else:
            target = profiles[0]

        self._profile_bar.refresh(select=target)
        self._load_profile(target)

    def _maybe_prompt_resume(self):
        """If a queue checkpoint exists for the current profile, ask to resume.

        Mismatched profiles leave the checkpoint untouched — the user can
        switch to that profile and the prompt will fire next launch. A
        broken / older-version checkpoint is silently dropped via
        ``queue_checkpoint.load`` returning None (the file stays so the
        user can investigate manually).
        """
        from core import queue_checkpoint
        if not queue_checkpoint.exists():
            return
        payload = queue_checkpoint.load()
        if payload is None:
            return
        if payload.profile and payload.profile != self._current_profile_name:
            log.info(
                "Skipping resume prompt: checkpoint is for profile %r, current is %r",
                payload.profile, self._current_profile_name,
            )
            return

        remaining = payload.remaining
        if not remaining:
            # Nothing left to do — checkpoint is stale, drop it.
            queue_checkpoint.delete()
            return

        reply = QMessageBox.question(
            self, "Resume previous batch?",
            f"A previous batch was interrupted with {len(remaining)} asset(s) "
            f"still pending (profile: {payload.profile or 'unknown'}).\n\n"
            "Resume processing now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            queue_checkpoint.delete()
            return

        # Add the remaining assets to the queue and kick off processing.
        # The JobManager will be constructed with already_completed so its
        # checkpoint writes continue from where we left off.
        self._queue.add_to_queue(remaining)
        self._resume_completed = list(payload.completed)
        self._log.append(
            f"Resuming previous batch ({len(remaining)} pending)", "info"
        )
        self._process_queue()

    def _is_busy(self) -> bool:
        """Return True if any background operation is running."""
        if any(w.isRunning() for w in self._active_workers):
            return True
        if self._unpacker_panel.is_exporting:
            return True
        if self._job_manager and self._job_manager.isRunning():
            return True
        if self._pending_cache_writes > 0:
            return True
        return False

    def _cancel_active_ops(self):
        """Cancel whatever background operation is running."""
        for worker in list(self._active_workers):
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    log.exception("Worker cancel raised: %s", type(worker).__name__)
        if self._unpacker_panel.is_exporting:
            self._unpacker_panel.cancel_export()
        if self._job_manager and self._job_manager.isRunning():
            self._cancel_processing()

    def _save_current_profile(self):
        """Persist the current UI state into the active profile JSON.

        Path fields from the Unpacker tab (game_dir / unpack_output_dir /
        ue_version / mappings_path / aes_keys) are written back only when the
        profile has ``auto_save_paths`` enabled. Otherwise the on-disk profile
        is the source of truth and the Unpacker tab is purely a session-local
        editor — see the Manage Profiles dialog to mutate path fields.
        """
        name = self._current_profile_name
        if not name:
            return

        try:
            data = self._profile_manager.load_profile(name)
        except FileNotFoundError:
            data = {}
        except ProfileLoadError as e:
            # Refuse to silently overwrite a profile we couldn't read — that
            # would clobber the user's data with whatever the panels have in
            # memory. Surface the error and bail.
            log.error("Cannot save profile '%s': %s", name, e)
            self._log.append(f"Cannot save profile '{name}': {e}", "error")
            return

        # Always merge picker state (psk_processed) — that's a record of work
        # done, not a path the user is editing.
        data.update(self._psk_picker.collect_for_profile())

        # Path fields: only auto-merge when explicitly opted-in per profile.
        if data.get("auto_save_paths", False):
            data.update(self._unpacker_panel.collect_for_profile())

        # blender_output_dir falls back to the legacy global key for users
        # who haven't yet set it via Manage Profiles.
        data["blender_output_dir"] = data.get("blender_output_dir", "") or config.get("output_dir")

        # Scan cache file is keyed by the saved game_dir, not whatever the
        # Unpacker tab has typed in right now.
        game_dir = data.get("game_dir", "")
        if game_dir:
            import hashlib
            folder_hash = hashlib.md5(game_dir.encode()).hexdigest()[:12]
            data["scan_cache_file"] = f"scan_{folder_hash}.json"

        # Persist colour scheme into the profile
        data["color_scheme"] = config.get("color_scheme") or theme.current_scheme_name()

        self._profile_manager.save_profile(name, data)
        log.debug("Saved profile: %s", name)

    def _open_profile_dialog(self):
        """Open the Manage Profiles dialog and react to its outcomes."""
        from gui.profile_dialog import ProfileDialog

        # Save in-flight state first so it isn't lost if the user renames /
        # deletes the active profile.
        self._save_current_profile()

        dlg = ProfileDialog(
            self._profile_manager,
            current_profile=self._current_profile_name,
            parent=self,
        )

        # Track outcomes so we can surface the right log line + reload state.
        renames: list[tuple[str, str]] = []
        creates: list[str] = []
        deletes: list[str] = []
        dlg.profile_renamed.connect(lambda old, new: renames.append((old, new)))
        dlg.profile_created.connect(lambda n: creates.append(n))
        dlg.profile_deleted.connect(lambda n: deletes.append(n))

        result = dlg.exec()

        # Always refresh the dropdown — names may have changed even if the
        # user clicked Cancel (New / Rename / Delete commit immediately).
        active = self._current_profile_name
        for old, new in renames:
            if active == old:
                active = new
                config.set("active_profile", new)
                self.setWindowTitle(f"EfficientAssetRipper v{__version__} — {new}")
            self._log.append(f"Renamed profile: {old} → {new}", "info")
        for n in creates:
            self._log.append(f"Created profile: {n}", "info")
        for n in deletes:
            self._log.append(f"Deleted profile: {n}", "info")
            if active == n:
                # Active profile was deleted — fall back to the first remaining
                remaining = self._profile_manager.list_profiles()
                active = remaining[0] if remaining else ""

        # If OK was pressed, on-disk path values may have changed for the
        # active profile (e.g. user edited Mounted Folder). Reload state so
        # the Unpacker panel and config bridges reflect the new values.
        needs_reload = (
            result == 1  # QDialog.Accepted
            or active != self._current_profile_name
        )
        if active and needs_reload:
            self._load_profile(active)

        self._profile_bar.refresh(select=active)

    def _load_profile(self, name: str):
        """Load a profile from disk and push its state to all panels."""
        try:
            data = self._profile_manager.load_profile(name)
        except FileNotFoundError:
            self._log.append(f"Profile '{name}' not found", "warning")
            return
        except ProfileLoadError as e:
            log.error("Failed to load profile '%s': %s", name, e)
            QMessageBox.critical(
                self, "Profile load failed",
                f"Could not read profile '{name}'.\n\n{e}\n\n"
                "Close any program that may be holding the file open, "
                "or open the profiles folder to inspect it manually.",
            )
            self._log.append(f"Failed to load profile '{name}': {e}", "error")
            return

        self._current_profile_name = name
        config.set("active_profile", name)

        # Push state to panels
        self._unpacker_panel.load_from_profile(data)

        # Clear queue and log when switching profiles
        self._queue.clear_queue()
        self._log.clear()

        # Also set game_folder in global config so other parts (scanner, etc.) can read it
        game_dir = data.get("game_dir", "")
        config.set("game_folder", game_dir)
        config.set("unpack_output_dir", data.get("unpack_output_dir", ""))
        config.set("unpack_ue_version", data.get("ue_version", "GAME_UE5_4"))

        # Apply profile's colour scheme if it has one
        profile_scheme = data.get("color_scheme", "")
        if profile_scheme:
            config.set("color_scheme", profile_scheme)
            app = QApplication.instance()
            if app:
                theme.apply(app, profile_scheme)

        blender_out = data.get("blender_output_dir", "")
        if blender_out:
            config.set("output_dir", blender_out)

        # Load scan cache for this profile
        self._browser.set_assets([])  # clear first
        scan_cache_file = data.get("scan_cache_file", "")
        if game_dir and scan_cache_file:
            result = load_scan_cache(game_dir)
            if result is not None:
                assets, timestamp = result
                import datetime
                age = datetime.datetime.now() - datetime.datetime.fromtimestamp(timestamp)
                if age.days > 0:
                    age_str = f"{age.days}d ago"
                elif age.seconds >= 3600:
                    age_str = f"{age.seconds // 3600}h ago"
                else:
                    age_str = f"{age.seconds // 60}m ago"

                self._browser.set_assets(assets)
                ready = sum(1 for a in assets if a.status == "ready")
                msg = f"[{name}] Loaded {len(assets)} cached assets ({ready} ready, scanned {age_str})"
                self._log.append(msg, "info")
                self._statusbar.showMessage(msg)

                # Seed picker with already-processed paths
                processed = [a.psk_path for a in assets if a.processed]
                if processed:
                    self._psk_picker.mark_processed(processed)

        # Load PSK picker state (after cache so processed lists merge correctly)
        self._psk_picker.load_from_profile(data)

        self.setWindowTitle(f"EfficientAssetRipper v{__version__} — {name}")

    def _switch_profile(self, name: str):
        """Save current profile, then load the new one."""
        if name == self._current_profile_name:
            return
        self._save_current_profile()
        self._load_profile(name)
        self._profile_bar.set_current(name)

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def _add_browser_to_queue(self, assets: list[AssetEntry]):
        """Add assets from the browser's 'Add to Queue' button."""
        count = len(assets)
        self._queue.add_to_queue(assets)
        self._log.append(f"Added {count} assets to queue from browser", "info")
        # Switch to queue tab so user sees the result
        self._right_tabs.setCurrentIndex(0)

    def _add_picker_to_queue(self, paths: list[Path]):
        """Resolve PSK paths from the picker and add to queue."""
        # Use unpack_output_dir (where exported PSK/props/TGA files live) when
        # set; fall back to game_folder for single-directory setups.
        game_folder = config.get("unpack_output_dir") or config.get("game_folder")
        if not game_folder:
            QMessageBox.warning(self, "No Game Folder", "Set the game folder under Manage Profiles first.")
            return

        dll_path = config.get("everything_dll") or None
        try:
            sdk = get_sdk(dll_path)
        except EverythingError as e:
            QMessageBox.critical(self, "Everything SDK Error", str(e))
            return

        presets = config.load_presets()
        scanner = AssetScanner(game_folder, presets, sdk)

        self._log.append(f"Resolving {len(paths)} picked PSK files...", "info")
        self._statusbar.showMessage(f"Resolving {len(paths)} picked files...")

        # Build stub AssetEntry objects for each path
        from core.classifier import classify
        entries: list[AssetEntry] = []
        for p in paths:
            e = AssetEntry(psk_path=p, name=p.stem)
            cat = classify(p, game_folder)
            e.category = cat.category
            e.subcategory = cat.subcategory
            entries.append(e)

        self._picker_entries = entries
        self._picker_resolved_entries: list[AssetEntry] = []
        self._picker_worker = _PickerResolveWorker(scanner, entries, self)
        self._picker_worker.progress.connect(self._on_scan_progress)
        self._picker_worker.entry_resolved.connect(self._on_picker_entry_resolved)
        self._picker_worker.finished.connect(self._on_picker_resolved)
        self._picker_worker.error.connect(self._on_scan_error)
        self._track_worker(self._picker_worker)
        self._queue.set_resolving(True)
        self._right_tabs.setCurrentIndex(0)
        self._picker_worker.start()

    @Slot(object)
    def _on_picker_entry_resolved(self, entry: AssetEntry):
        """Add each resolved entry to the queue immediately as it finishes."""
        self._picker_resolved_entries.append(entry)
        self._queue.add_to_queue([entry])

    @Slot(int, int)
    def _on_picker_resolved(self, resolved: int, still_incomplete: int):
        self._queue.set_resolving(False)
        entries = self._picker_resolved_entries
        self._merge_entries_into_browser(entries)
        msg = f"Added {len(entries)} picked assets to queue ({resolved} ready)"
        self._log.append(msg, "info")
        self._statusbar.showMessage(msg)

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def _process_queue(self):
        """Process all pending items currently in the queue."""
        pending = self._queue.get_pending_assets()
        if pending:
            self._start_processing(pending)
        else:
            QMessageBox.information(
                self, "Nothing to Process",
                "No pending items in the queue. Add assets first."
            )

    def _start_processing(self, assets: list[AssetEntry]):
        if not assets:
            QMessageBox.information(
                self, "Nothing Selected",
                "Select assets in the browser first."
            )
            return

        blender_exe = config.get("blender_exe")
        if not blender_exe:
            QMessageBox.warning(
                self, "Blender Not Set",
                "Set the Blender executable path in Settings.\n\n"
                "Until then, Process Queue and the Blend Combiner tab are disabled."
            )
            return

        output_dir = config.get("output_dir")
        if not output_dir:
            QMessageBox.warning(
                self, "Output Directory Not Set",
                "Set the output directory under Manage Profiles."
            )
            return

        addon_name = config.get("psk_addon_name")
        timeout = config.get_int("timeout_seconds") or 120

        # Set up queue panel for this batch
        batch_offset = self._queue.get_pending_offset()
        self._queue.begin_processing(batch_offset, len(assets))
        self._queue.set_processing(True)

        self._log.append(
            f"Starting batch: {len(assets)} assets → {output_dir}", "info"
        )

        self._job_manager = JobManager(
            assets=assets,
            blender_exe=blender_exe,
            output_dir=output_dir,
            addon_name=addon_name,
            timeout=timeout,
            parent=self,
            profile_name=self._current_profile_name,
            already_completed=getattr(self, "_resume_completed", None),
        )
        # The completed-paths carry-over only applies to the very next batch;
        # subsequent runs start fresh.
        self._resume_completed = None

        # Connect signals
        self._job_manager.job_started.connect(self._queue.on_job_started)
        self._job_manager.job_completed.connect(self._queue.on_job_completed)
        self._job_manager.job_progress.connect(self._queue.on_job_progress)
        self._job_manager.queue_finished.connect(self._queue.on_queue_finished)
        self._job_manager.log_message.connect(self._log.append)
        self._job_manager.queue_finished.connect(self._on_processing_done)

        self._job_manager.start()

    def _cancel_processing(self):
        if self._job_manager and self._job_manager.isRunning():
            self._job_manager.cancel()
            self._log.append("Cancelling batch...", "warning")
        picker_worker = getattr(self, "_picker_worker", None)
        if picker_worker and picker_worker.isRunning():
            picker_worker.cancel()
            self._log.append("Cancelling asset resolve...", "warning")
            self._statusbar.showMessage("Cancelling resolve...")

    @Slot(int, int, int)
    def _on_processing_done(self, total: int, succeeded: int, failed: int):
        self._statusbar.showMessage(
            f"Batch done: {succeeded}/{total} succeeded, {failed} failed"
        )
        # Refresh browser to show blend paths and processed status
        self._browser.refresh_tree()
        # Re-save cache with updated blend_path / processed data
        self._save_cache()
        # Mark processed items in PSK picker so they can't be re-queued
        processed = [
            a.psk_path for a in self._browser.assets if a.processed
        ]
        if processed:
            self._psk_picker.mark_processed(processed)

    # ------------------------------------------------------------------
    # Reprocess
    # ------------------------------------------------------------------

    def _reprocess_asset(self, asset):
        """Reprocess a single asset (overwrites existing .blend)."""
        asset.processed = False
        self._queue.add_to_queue([asset])
        self._right_tabs.setCurrentIndex(0)
        self._log.append(f"Queued for reprocessing: {asset.name}", "info")

    # ------------------------------------------------------------------
    # Browser / cache helpers
    # ------------------------------------------------------------------

    def _merge_entries_into_browser(self, entries: list):
        """Merge entries into the browser's asset list (de-duplicate by psk_path)."""
        existing = {str(a.psk_path): a for a in self._browser._assets}
        added = 0
        for e in entries:
            key = str(e.psk_path)
            if key not in existing:
                self._browser._assets.append(e)
                existing[key] = e
                added += 1
            else:
                # Update the existing entry with any new data
                ex = existing[key]
                if not ex.materials and e.materials:
                    ex.materials = e.materials
                    ex.total_textures = e.total_textures
                    ex.missing_textures = e.missing_textures
                    ex.mesh_props_found = e.mesh_props_found
        if added:
            self._browser._populate_category_filter()
        self._browser.refresh_tree()

    def _save_cache(self):
        """Save the browser's current assets to the scan cache and update profile."""
        game_folder = config.get("game_folder")
        assets = self._browser.assets
        if game_folder and assets:
            self._save_cache_async(assets, game_folder)
        self._save_current_profile()

    def _save_cache_async(
        self,
        assets: list,
        game_folder: str,
        on_done_msg: str | None = None,
    ) -> None:
        """Submit a cache write to the global QThreadPool. Tracks pending count."""
        self._pending_cache_writes += 1
        self._statusbar.showMessage("Saving cache...", 1500)

        def _done():
            # _on_cache_write_done runs in the worker thread; bounce to GUI
            # thread via QTimer.singleShot so signal-slot invariants hold.
            QTimer.singleShot(0, lambda: self._on_cache_write_done(on_done_msg))

        runnable = _CacheWriteRunnable(assets, game_folder, on_done=_done)
        QThreadPool.globalInstance().start(runnable)

    def _on_cache_write_done(self, msg: str | None):
        if self._pending_cache_writes > 0:
            self._pending_cache_writes -= 1
        if msg:
            self._log.append(msg, "info")

    def _on_browser_delete(self, assets: list):
        """Handle deletion of assets from the browser — save updated cache."""
        names = [a.name for a in assets]
        self._log.append(f"Removed {len(assets)} asset(s): {', '.join(names)}", "info")
        # Un-mark deleted assets in the picker so they become selectable again
        self._psk_picker.unmark_processed([a.psk_path for a in assets])
        self._save_cache()

    # ------------------------------------------------------------------
    # Re-scan incomplete entries
    # ------------------------------------------------------------------

    def _rescan_selected(self, entries: list):
        game_folder = config.get("game_folder")
        if not game_folder:
            QMessageBox.warning(self, "No Game Folder", "Set the game folder under Manage Profiles first.")
            return

        dll_path = config.get("everything_dll") or None
        try:
            sdk = get_sdk(dll_path)
        except EverythingError as e:
            QMessageBox.critical(self, "Everything SDK Error", str(e))
            return

        presets = config.load_presets()
        scanner = AssetScanner(game_folder, presets, sdk)

        self._log.append(f"Re-scanning {len(entries)} incomplete entries...", "info")
        self._scan_btn.setEnabled(False)
        self._statusbar.showMessage(f"Re-scanning {len(entries)} entries...")

        self._rescan_worker = RescanWorker(scanner, entries, self)
        self._rescan_worker.progress.connect(self._on_scan_progress)
        self._rescan_worker.finished.connect(self._on_rescan_finished)
        self._rescan_worker.error.connect(self._on_scan_error)
        self._track_worker(self._rescan_worker)
        self._rescan_worker.start()

    @Slot(int, int)
    def _on_rescan_finished(self, resolved: int, still_incomplete: int):
        self._scan_btn.setEnabled(True)
        self._browser.refresh_tree()
        msg = f"Re-scan done: {resolved} resolved, {still_incomplete} still incomplete"
        self._log.append(msg, "success" if still_incomplete == 0 else "info")
        self._statusbar.showMessage(msg)

        # Update cache
        game_folder = config.get("game_folder")
        assets = self._browser.assets
        if game_folder and assets:
            self._save_cache_async(
                assets, game_folder, on_done_msg="Cache updated"
            )

    # ------------------------------------------------------------------
    # Unpacker hand-off
    # ------------------------------------------------------------------

    def _on_psks_extracted(self, psk_paths: list):
        """Receive extracted PSK files from the Unpacker and add to queue."""
        from core.classifier import classify

        game_folder = config.get("game_folder") or config.get("unpack_output_dir") or ""
        entries = []
        for p in psk_paths:
            e = AssetEntry(psk_path=p, name=p.stem)
            if game_folder:
                cat = classify(p, game_folder)
                e.category = cat.category
                e.subcategory = cat.subcategory
            entries.append(e)

        self._queue.add_to_queue(entries)
        self._merge_entries_into_browser(entries)
        msg = f"Added {len(entries)} extracted PSK files to queue"
        self._log.append(msg, "info")
        self._statusbar.showMessage(msg)
        self._right_tabs.setCurrentIndex(0)

    def closeEvent(self, event):
        """Save profile and stop the CLI process on exit."""
        # Save the in-memory profile state up front. If we wait until *after*
        # cancellation, a partially-cancelled scan could lose its picker /
        # unpacker state on the way out.
        try:
            self._save_current_profile()
        except Exception:
            log.exception("save_current_profile during shutdown raised")

        running_workers = [w for w in self._active_workers if w.isRunning()]
        job_running = bool(self._job_manager and self._job_manager.isRunning())
        export_running = bool(self._unpacker_panel.is_exporting)

        if running_workers or job_running or export_running:
            reply = QMessageBox.question(
                self, "Cancel running operations?",
                "Background work is still running. Cancel and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        self._cancel_active_ops()

        # Wait for workers to drain. We give each worker a reasonable budget
        # before falling back to terminate(); QProgressDialog keeps the user
        # informed instead of hanging silently.
        progress = None
        if running_workers or job_running:
            progress = QProgressDialog("Stopping background work...", None, 0, 0, self)
            progress.setWindowModality(Qt.WindowModality.ApplicationModal)
            progress.setCancelButton(None)
            progress.setMinimumDuration(0)
            progress.show()
            QApplication.processEvents()

        for worker in running_workers:
            try:
                if not worker.wait(5000):
                    log.warning(
                        "Worker %s did not exit cleanly within 5s — terminating",
                        type(worker).__name__,
                    )
                    worker.terminate()
                    worker.wait(2000)
            except Exception:
                log.exception("Error waiting for worker %s", type(worker).__name__)

        if self._job_manager and self._job_manager.isRunning():
            try:
                if not self._job_manager.wait(5000):
                    log.warning("JobManager did not exit cleanly — terminating")
                    self._job_manager.terminate()
                    self._job_manager.wait(2000)
            except Exception:
                log.exception("Error waiting for JobManager")

        # Drain any in-flight cache writes (best-effort).
        try:
            QThreadPool.globalInstance().waitForDone(3000)
        except Exception:
            log.exception("waitForDone for cache writes raised")

        if progress is not None:
            progress.close()

        try:
            self._unpacker_panel.shutdown()
        except Exception:
            log.exception("UnpackerPanel.shutdown raised")
        # Block briefly on the update-checkers so a long-running HTTP timeout
        # can't keep their QThreads alive past the QObject lifetime.
        for checker in (self._update_checker, self._manual_update_checker):
            if checker is not None:
                try:
                    checker.shutdown(timeout_ms=2000)
                except Exception:  # noqa: BLE001
                    log.debug("update_checker.shutdown raised", exc_info=True)
        super().closeEvent(event)
