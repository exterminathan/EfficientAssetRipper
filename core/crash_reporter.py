"""Top-level crash reporter.

Captures uncaught Python exceptions, fatal Qt messages, and explicit
subprocess-fatal hooks. For each crash:

1. Writes a structured JSON report to ``logs/crash_<ts>.json``.
2. Logs the traceback (the existing ``SecretRedactingFilter`` redacts AES
   keys / hex blobs from the message).
3. Pops a small dialog with three buttons — copy report to clipboard,
   open a prefilled GitHub issue, or continue.

No data leaves the machine unless the user clicks "Open GitHub issue" —
this is opt-in by design. There is no auto-phone-home.

Activate once during startup, after :func:`core.log_redaction.install_global_redactor`
so report contents go through the same redaction filter as normal logs.
"""

from __future__ import annotations

import datetime
import json
import logging
import platform
import sys
import traceback
import urllib.parse
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import qInstallMessageHandler, QtMsgType

from _base import base_dir
from _version import __version__

log = logging.getLogger(__name__)

_GH_REPO = "exterminathan/EfficientAssetRipper"
# GitHub silently truncates very long ?body= URLs; keep total under this.
_GH_URL_LIMIT = 7000
_LOG_TAIL_KEEP = 200

_LOGS_DIR = base_dir() / "logs"


# ---------------------------------------------------------------------------
# Module state (set by install())
# ---------------------------------------------------------------------------

_installed = False
_active_profile_provider: Optional[Callable[[], str]] = None
_log_ring: "deque[str]" = deque(maxlen=_LOG_TAIL_KEEP)
_dialog_factory: Optional[Callable[[dict, Path], None]] = None


class _RingHandler(logging.Handler):
    """Keep the most recent log records so they can be attached to crashes.

    Capped at ``_LOG_TAIL_KEEP`` entries; oldest fall off. Records are
    formatted to plain strings on the way in so the buffer doesn't pin
    arbitrary objects in memory.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _log_ring.append(self.format(record))
        except Exception:
            pass


def install(
    *,
    active_profile_provider: Optional[Callable[[], str]] = None,
    dialog_factory: Optional[Callable[[dict, Path], None]] = None,
) -> None:
    """Register sys.excepthook + Qt message handler. Idempotent.

    ``active_profile_provider`` is called at crash time to attach the
    current profile name to the report; pass ``None`` if you don't have
    one yet. ``dialog_factory`` lets tests replace the GUI dialog with a
    stub.
    """
    global _installed, _active_profile_provider, _dialog_factory
    if _installed:
        return

    _active_profile_provider = active_profile_provider
    _dialog_factory = dialog_factory or _default_dialog

    sys.excepthook = _excepthook
    qInstallMessageHandler(_qt_msg_handler)

    # Attach the ring buffer to the root logger so we capture log lines
    # from every module from this point forward.
    root = logging.getLogger()
    if not any(isinstance(h, _RingHandler) for h in root.handlers):
        root.addHandler(_RingHandler())

    _installed = True
    log.debug("crash_reporter installed")


def is_installed() -> bool:
    return _installed


def reset_for_tests() -> None:
    """Tear down installed state. **Tests only.**"""
    global _installed, _active_profile_provider, _dialog_factory
    _installed = False
    _active_profile_provider = None
    _dialog_factory = None
    _log_ring.clear()
    sys.excepthook = sys.__excepthook__
    qInstallMessageHandler(None)
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, _RingHandler):
            root.removeHandler(h)


# ---------------------------------------------------------------------------
# Programmatic entry points
# ---------------------------------------------------------------------------

def report_subprocess_crash(name: str, detail: str, *, show_dialog: bool = True) -> Path:
    """Record a fatal subprocess failure (e.g. Blender exe missing).

    Per-asset failures should NOT call this — they belong in the batch log.
    """
    report = build_report(f"subprocess:{name}", detail)
    path = _write_report(report)
    log.error("Subprocess crash (%s) — report: %s", name, path.name)
    if show_dialog:
        _safe_show(report, path)
    return path


def build_report(crash_type: str, detail: str) -> dict:
    return {
        "version": __version__,
        "crash_type": crash_type,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "qt": _qt_version(),
        "platform": platform.platform(),
        "active_profile": _safe_call(_active_profile_provider) or "",
        "log_tail": list(_log_ring),
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

def _excepthook(exc_type, exc_value, exc_tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        # Let Ctrl+C behave the way Python expects.
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    report = build_report("uncaught_exception", tb)
    path = _write_report(report)
    log.error("Uncaught exception (report: %s)\n%s", path.name, tb)
    _safe_show(report, path)


def _qt_msg_handler(msg_type, ctx, message) -> None:
    if msg_type == QtMsgType.QtFatalMsg:
        report = build_report("qt_fatal", message)
        path = _write_report(report)
        log.error("Qt fatal: %s (report: %s)", message, path.name)
        _safe_show(report, path)
    elif msg_type == QtMsgType.QtCriticalMsg:
        log.error("Qt critical: %s", message)
    elif msg_type == QtMsgType.QtWarningMsg:
        log.warning("Qt: %s", message)
    else:
        log.debug("Qt: %s", message)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _qt_version() -> str:
    try:
        from PySide6.QtCore import qVersion
        return qVersion()
    except Exception:
        return "?"


def _safe_call(fn, *a):
    if fn is None:
        return None
    try:
        return fn(*a)
    except Exception:
        log.exception("crash_reporter helper raised")
        return None


def _safe_show(report: dict, path: Path) -> None:
    """Never let the dialog raise — that would loop the excepthook."""
    if _dialog_factory is None:
        return
    try:
        _dialog_factory(report, path)
    except Exception:
        log.exception("Crash dialog itself raised; ignoring")


def _write_report(report: dict) -> Path:
    try:
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.exception("Failed to create logs directory for crash report")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = _LOGS_DIR / f"crash_{ts}.json"
    try:
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        log.exception("Failed to write crash report")
    return path


def github_issue_url(report: dict) -> str:
    """Build a prefilled new-issue URL for ``_GH_REPO``."""
    body_lines = [
        "<!-- Auto-generated crash report — feel free to edit before submitting. -->",
        "",
        f"**Version:** {report['version']}",
        f"**Crash type:** `{report['crash_type']}`",
        f"**When:** {report['timestamp']}",
        f"**Python:** {report['python']}  **Qt:** {report['qt']}",
        f"**Platform:** {report['platform']}",
        "",
        "### What you were doing",
        "<!-- Add a sentence about what you were doing when this happened. -->",
        "",
        "### Detail",
        "```",
        report["detail"],
        "```",
        "",
        "### Recent log",
        "```",
        "\n".join(report.get("log_tail", [])[-30:]) or "(no log tail captured)",
        "```",
    ]
    body = "\n".join(body_lines)
    title = f"Crash: {report['crash_type']}"

    qs = urllib.parse.urlencode({"title": title, "body": body})
    full = f"https://github.com/{_GH_REPO}/issues/new?{qs}"
    if len(full) <= _GH_URL_LIMIT:
        return full

    truncated = (
        body[: _GH_URL_LIMIT // 2]
        + "\n\n_Body truncated — attach `logs/crash_*.json` to the issue for the full report._"
    )
    qs = urllib.parse.urlencode({"title": title, "body": truncated})
    return f"https://github.com/{_GH_REPO}/issues/new?{qs}"


# ---------------------------------------------------------------------------
# Default dialog (lazy GUI imports)
# ---------------------------------------------------------------------------

def _default_dialog(report: dict, report_path: Path) -> None:
    """Show the standard crash dialog. Skipped when no QApplication exists."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices, QGuiApplication
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QHBoxLayout,
        QLabel,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
    )

    if QApplication.instance() is None:
        # No GUI yet — happens during very early startup. The on-disk JSON
        # report is enough; nothing to render.
        return

    dlg = QDialog()
    dlg.setWindowTitle("EfficientAssetRipper — Something went wrong")
    dlg.setMinimumSize(640, 460)

    outer = QVBoxLayout(dlg)
    outer.addWidget(QLabel(
        "<b>An unexpected error occurred.</b><br>"
        f"A report has been written to:<br><code>{report_path}</code><br><br>"
        "If you'd like, you can share it by opening a GitHub issue. "
        "Nothing is sent automatically."
    ))

    detail_view = QPlainTextEdit()
    detail_view.setReadOnly(True)
    detail_view.setPlainText(report.get("detail", ""))
    outer.addWidget(detail_view, 1)

    btns = QHBoxLayout()
    copy_btn = QPushButton("Copy report")
    issue_btn = QPushButton("Open GitHub issue")
    cont_btn = QPushButton("Continue")
    btns.addWidget(copy_btn)
    btns.addWidget(issue_btn)
    btns.addStretch()
    btns.addWidget(cont_btn)
    outer.addLayout(btns)

    def _copy() -> None:
        QGuiApplication.clipboard().setText(json.dumps(report, indent=2))

    def _open_issue() -> None:
        QDesktopServices.openUrl(QUrl(github_issue_url(report)))

    copy_btn.clicked.connect(_copy)
    issue_btn.clicked.connect(_open_issue)
    cont_btn.clicked.connect(dlg.accept)
    dlg.exec()
