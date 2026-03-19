"""EfficientAssetRipper — UE5 Asset Assembler entry point."""

import sys
import logging

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPointF, QRect, QRectF, QTimer
from PySide6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QBrush, QPen,
    QLinearGradient, QPolygonF,
)

from gui.main_window import MainWindow
from gui.splash import SplashScreen
import gui.theme as theme
import config

# ── Toggle the startup animation on/off ──────────────────────────────────────
SHOW_SPLASH = True


def _make_icon() -> QIcon:
    """Gem/crystal icon rendered at multiple sizes for crisp display."""
    c = theme.current_scheme()
    icon = QIcon()

    for size in (16, 32, 48, 64, 256):
        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        s = float(size)
        cx, cy = s / 2, s / 2
        pad = s * 0.05
        radius = s * 0.18

        # --- dark rounded background ---
        bg = QLinearGradient(0.0, 0.0, s, s)
        bg.setColorAt(0.0, QColor(c["bg_darkest"]))
        bg.setColorAt(1.0, QColor(c["bg_dark"]))
        p.setBrush(QBrush(bg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(pad, pad, s - 2 * pad, s - 2 * pad), radius, radius)

        # --- gem diamond shape ---
        dh = s * 0.36   # half-height
        dw = s * 0.27   # half-width

        gem = QPolygonF([
            QPointF(cx,        cy - dh),   # top
            QPointF(cx + dw,   cy),         # right
            QPointF(cx,        cy + dh),   # bottom
            QPointF(cx - dw,   cy),         # left
        ])
        gem_grad = QLinearGradient(cx - dw, cy - dh, cx + dw, cy + dh)
        gem_grad.setColorAt(0.0, QColor(c["accent"]))
        gem_grad.setColorAt(0.45, QColor(c["accent_hover"]))
        gem_grad.setColorAt(1.0,  QColor(c["accent_muted"]))
        p.setBrush(QBrush(gem_grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(gem)

        # --- inner facet lines (only worth drawing at >=32 px) ---
        if size >= 32:
            lw = max(1.0, s * 0.025)
            p.setPen(QPen(QColor(255, 255, 255, 190), lw))
            p.drawLine(QPointF(cx, cy - dh), QPointF(cx + dw, cy))
            p.drawLine(QPointF(cx, cy - dh), QPointF(cx - dw * 0.55, cy - dh * 0.25))
            p.setPen(QPen(QColor(255, 255, 255, 80), lw * 0.7))
            p.drawLine(QPointF(cx - dw, cy), QPointF(cx + dw, cy))

        # --- outer glow ring ---
        if size >= 32:
            gw = max(1.5, s * 0.055)
            glow_pen = QPen(QColor(c["accent"]).lighter(120), gw)
            glow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(glow_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            scale = 1.22
            p.drawPolygon(QPolygonF([
                QPointF(cx,              cy - dh * scale),
                QPointF(cx + dw * scale, cy),
                QPointF(cx,              cy + dh * scale),
                QPointF(cx - dw * scale, cy),
            ]))

        # --- tiny accent dots at cardinal corners (>=48 px) ---
        if size >= 48:
            dot_r = s * 0.035
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(c["accent"]).lighter(140)))
            for dx, dy in ((0, -dh * 1.32), (dw * 1.32, 0),
                           (0, dh * 1.32), (-dw * 1.32, 0)):
                p.drawEllipse(QRectF(cx + dx - dot_r, cy + dy - dot_r,
                                     dot_r * 2, dot_r * 2))

        p.end()
        icon.addPixmap(px)

    return icon


def _load_saved_custom_schemes():
    """Restore user-defined colour schemes from QSettings/config."""
    import json
    from gui.color_schemes import register_custom_scheme
    raw = config.get("custom_schemes")
    if not raw or raw == "{}":
        return
    try:
        custom = json.loads(raw)
        for name, colors in custom.items():
            register_custom_scheme(name, colors)
    except (json.JSONDecodeError, TypeError):
        pass


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Windows: give this process its own identity so the taskbar shows
    # our icon instead of the generic Python interpreter icon.
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "EfficientAssetRipper.App.1"
        )

    app = QApplication(sys.argv)
    app.setApplicationName("EfficientAssetRipper")
    app.setOrganizationName("EfficientAssetRipper")

    # Apply centralised theme (palette + stylesheet)
    _load_saved_custom_schemes()
    saved_scheme = config.get("color_scheme") or None
    theme.apply(app, saved_scheme)

    icon = _make_icon()
    app.setWindowIcon(icon)

    # Build the main window (hidden) so it loads during the splash
    window = MainWindow()
    window.setWindowIcon(icon)

    # Set default size and centre on screen
    _win_w, _win_h = 1600, 950
    screen_rect = app.primaryScreen().availableGeometry()
    _win_x = screen_rect.x() + (screen_rect.width() - _win_w) // 2
    _win_y = screen_rect.y() + (screen_rect.height() - _win_h) // 2
    window.setGeometry(_win_x, _win_y, _win_w, _win_h)

    if SHOW_SPLASH:
        def _show_main():
            window.show()
        splash = SplashScreen(
            finish_callback=_show_main,
            target_rect=QRect(_win_x, _win_y, _win_w, _win_h),
        )
        splash.start()
    else:
        window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
