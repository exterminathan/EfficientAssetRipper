"""First-run setup wizard.

Shown only when ``config.get("setup_complete") != "1"``. Walks the user
through dependency detection, game folder + AES keys, and the output
directory. The user can hit "Skip Setup" at any time and land in the main
window with whatever defaults are in place.

Wraps the existing ``SettingsDialog`` validators where possible — this
module is augmentation, not a re-implementation.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

import config
from _base import base_dir
from _version import __version__
from gui.settings_panel import PathPicker, SettingsDialog

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dependency probes (split out so tests can patch them)
# ---------------------------------------------------------------------------

def detect_blender() -> tuple[bool, str]:
    """Return (found, detail). Detail is the resolved path or a hint."""
    path = config.get("blender_exe") or SettingsDialog._auto_detect_blender()
    if path and Path(path).is_file():
        return True, path
    return False, "Not found in default install locations"


def detect_everything() -> tuple[bool, str]:
    """Return (found, detail). Detail is the DLL path or a hint."""
    path = config.get("everything_dll") or SettingsDialog._auto_detect_everything()
    if path and Path(path).is_file():
        return True, path
    return False, "Everything64.dll not found — install Everything desktop"


def detect_dotnet() -> tuple[bool, str]:
    """Return (found, detail). Probes `dotnet --version` on PATH."""
    if not shutil.which("dotnet"):
        return False, ".NET runtime not on PATH"
    try:
        proc = subprocess.run(
            ["dotnet", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        ver = proc.stdout.strip().splitlines()[0] if proc.stdout else "unknown"
        return True, f".NET {ver}"
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"dotnet check failed: {e}"


# ---------------------------------------------------------------------------
# Wizard pages
# ---------------------------------------------------------------------------

class _WelcomePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle(f"Welcome to EfficientAssetRipper v{__version__}")
        self.setSubTitle("A few quick questions before we get started.")

        layout = QVBoxLayout(self)

        intro = QLabel(
            "EfficientAssetRipper turns Unreal Engine 5 game files into "
            "ready-to-use Blender scenes — meshes imported, PBR materials "
            "wired, .blend files saved.\n\n"
            "This wizard will:\n"
            "  • Detect the tools it needs (Blender, Everything, .NET runtime)\n"
            "  • Help you pick a game folder and output directory\n\n"
            "You can skip this wizard at any time and configure things "
            "manually from File → Settings."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        legal = QLabel(
            "<b>Legal:</b> for use only on games you legally own. "
            'See <a href="https://github.com/exterminathan/EfficientAssetRipper#-legal">'
            "the README</a> for the full disclaimer."
        )
        legal.setOpenExternalLinks(True)
        legal.setWordWrap(True)
        layout.addWidget(legal)

        layout.addStretch()


class _DependencyPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Dependency check")
        self.setSubTitle(
            "We need three external tools. Anything missing here can be "
            "installed and re-detected with the Re-check button."
        )

        layout = QVBoxLayout(self)

        self._rows: dict[str, dict] = {}
        for key, label, hint_url in (
            ("blender", "Blender 4.0+", "https://www.blender.org/download/"),
            ("everything", "Everything (must be running)", "https://www.voidtools.com/"),
            ("dotnet", ".NET 8.0 Runtime", "https://dotnet.microsoft.com/download/dotnet/8.0"),
        ):
            row = QHBoxLayout()
            status = QLabel("…")
            status.setFixedWidth(20)
            font = QFont()
            font.setBold(True)
            status.setFont(font)
            name = QLabel(label)
            name.setMinimumWidth(220)
            detail = QLabel("")
            detail.setWordWrap(True)
            link = QLabel(f'<a href="{hint_url}">Download</a>')
            link.setOpenExternalLinks(True)
            link.setFixedWidth(80)
            row.addWidget(status)
            row.addWidget(name)
            row.addWidget(detail, stretch=1)
            row.addWidget(link)
            layout.addLayout(row)
            self._rows[key] = {"status": status, "detail": detail}

        layout.addSpacing(8)
        self._recheck_btn = QPushButton("Re-check")
        self._recheck_btn.clicked.connect(self.recheck)
        layout.addWidget(self._recheck_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addStretch()

    def initializePage(self):
        self.recheck()

    def recheck(self):
        for key, probe in (
            ("blender", detect_blender),
            ("everything", detect_everything),
            ("dotnet", detect_dotnet),
        ):
            ok, detail = probe()
            row = self._rows[key]
            row["status"].setText("✓" if ok else "✗")
            row["status"].setStyleSheet(
                "color: #4caf50;" if ok else "color: #e53935;"
            )
            row["detail"].setText(detail)

        # If Blender was detected, persist it so the rest of the wizard
        # (and the main window) can use it without prompting again.
        ok, path = detect_blender()
        if ok and not config.get("blender_exe"):
            config.set("blender_exe", path)
        ok, path = detect_everything()
        if ok and not config.get("everything_dll"):
            config.set("everything_dll", path)


class _GameFolderPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Game folder")
        self.setSubTitle(
            "Point at the directory containing your game's .pak / .utoc "
            "archives, or a folder of already-extracted assets. You can "
            "leave this blank and configure it later per-profile."
        )

        layout = QVBoxLayout(self)
        self.picker = PathPicker(mode="folder")
        self.picker.setText(config.get("game_folder"))
        layout.addWidget(self.picker)

        hint = QLabel(
            "Examples:\n"
            "  • Steam games: ...\\steamapps\\common\\<game>\\<game>\\Content\\Paks\n"
            "  • Epic games: ...\\Epic Games\\<game>\\<game>\\Content\\Paks"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()

    def validatePage(self) -> bool:
        config.set("game_folder", self.picker.text())
        return True


class _OutputDirPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Output directory")
        self.setSubTitle("Where should processed .blend files be saved?")

        layout = QVBoxLayout(self)
        self.picker = PathPicker(mode="folder")
        default = config.get("output_dir") or str(base_dir() / "outputs")
        self.picker.setText(default)
        layout.addWidget(self.picker)

        hint = QLabel(
            "The default points to the 'outputs' folder next to the app. "
            "Pick somewhere with plenty of disk space — generated .blend "
            "scenes can grow large for big batches."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()

    def validatePage(self) -> bool:
        out = self.picker.text().strip()
        if out:
            try:
                Path(out).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                log.warning("Could not create output dir %s: %s", out, e)
            config.set("output_dir", out)
        return True


class _DonePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("All set!")
        self.setSubTitle("EfficientAssetRipper is ready to go.")

        layout = QVBoxLayout(self)
        msg = QLabel(
            "You can change any of these settings later from "
            "<b>File → Settings</b>, and create separate profiles per "
            "game from the profile bar at the top of the main window.\n\n"
            "Click <b>Finish</b> to launch the app."
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)
        layout.addStretch()


# ---------------------------------------------------------------------------
# Wizard shell
# ---------------------------------------------------------------------------

class SetupWizard(QWizard):
    """First-run setup wizard. Sets ``setup_complete=1`` on Finish."""

    skipped = Signal()
    completed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"EfficientAssetRipper v{__version__} — Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.WizardOption.HaveCustomButton1, True)
        self.setButtonText(QWizard.WizardButton.CustomButton1, "Skip Setup")
        self.setButtonLayout([
            QWizard.WizardButton.Stretch,
            QWizard.WizardButton.CustomButton1,
            QWizard.WizardButton.BackButton,
            QWizard.WizardButton.NextButton,
            QWizard.WizardButton.FinishButton,
            QWizard.WizardButton.CancelButton,
        ])
        self.customButtonClicked.connect(self._on_custom_button)

        self.addPage(_WelcomePage())
        self.addPage(_DependencyPage())
        self.addPage(_GameFolderPage())
        self.addPage(_OutputDirPage())
        self.addPage(_DonePage())

        self.setMinimumSize(640, 460)

    def _on_custom_button(self, which: int):
        if which == QWizard.WizardButton.CustomButton1:
            log.info("User skipped first-run setup")
            self.skipped.emit()
            self.reject()

    def accept(self):
        config.set("setup_complete", "1")
        log.info("First-run setup completed")
        self.completed.emit()
        super().accept()


def should_show_setup() -> bool:
    """Return True if the wizard should fire on this launch."""
    return config.get("setup_complete") != "1"
