"""Animated splash screen for EfficientAssetRipper.

Sequence
--------
1. Logo gem spins up from the bottom of the screen
2. Circle grows around the logo
3. Logo + circle scale up rapidly to fill the screen
4. A rectangle opens from the centre, revealing the main window

The splash is a frameless, translucent overlay that paints everything
itself.  It calls ``finish_callback`` when the animation completes so
the caller can show the real window underneath.
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSequentialAnimationGroup,
    Qt,
    QTimer,
    Property,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from _version import __version__
from gui.color_schemes import get_scheme, DEFAULT_SCHEME


# ---------------------------------------------------------------------------
# Animation phases encoded as a single 0‒1 float (``progress``)
#
#   0.00 – 0.30  Logo rise + spin
#   0.30 – 0.50  Circle grows around logo
#   0.50 – 0.70  Scale-up (logo + circle rush towards viewer)
#   0.70 – 1.00  Rectangle iris-open revealing the window
# ---------------------------------------------------------------------------

_PHASE_RISE_END = 0.30
_PHASE_CIRCLE_END = 0.50
_PHASE_ZOOM_END = 0.70
_PHASE_IRIS_END = 1.00

_DURATION_MS = 1800  # total animation length


def _ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def _ease_in_out(t: float) -> float:
    if t < 0.5:
        return 4 * t * t * t
    else:
        return 1 - (-2 * t + 2) ** 3 / 2


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


class SplashScreen(QWidget):
    """Animated splash overlay sized to the main window area."""

    def __init__(self, finish_callback: Callable[[], None], target_rect: QRect | None = None, scheme_name: str | None = None, parent=None):
        super().__init__(parent)
        self._target_rect = target_rect
        self._finish_cb = finish_callback
        self._colors = get_scheme(scheme_name or DEFAULT_SCHEME)

        # Frameless, stays on top, translucent background
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._progress = 0.0       # 0→1 drives the whole sequence
        self._finished = False

        # Animation
        self._anim = QPropertyAnimation(self, b"anim_progress")
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(_DURATION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.Linear)  # we ease manually per phase
        self._anim.valueChanged.connect(lambda _: self.update())
        self._anim.finished.connect(self._on_animation_done)

    # -- Qt property for QPropertyAnimation --
    def _get_anim_progress(self) -> float:
        return self._progress

    def _set_anim_progress(self, v: float):
        self._progress = v
        self.update()

    anim_progress = Property(float, _get_anim_progress, _set_anim_progress)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def start(self):
        """Show over the main window area and kick off the animation."""
        if self._target_rect is not None:
            self.setGeometry(self._target_rect)
            self.show()
        else:
            self.showFullScreen()
        self.raise_()
        self._anim.start()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        W = float(self.width())
        H = float(self.height())
        cx, cy = W / 2, H / 2
        t = self._progress

        c = self._colors

        # --- Full-screen background ---
        bg_alpha = 255
        if t >= _PHASE_ZOOM_END:
            # Iris phase: fade the background if we want, but keep opaque so iris reveals
            bg_alpha = 255
        bg = QColor(c["bg_darkest"])
        bg.setAlpha(bg_alpha)
        p.fillRect(0, 0, int(W), int(H), bg)

        # ── Phase 1: Logo rise + spin ────────────────────────────────
        if t < _PHASE_ZOOM_END:
            # Rise progress  (0→1 during rise phase)
            rise_t = _ease_out_cubic(min(t / _PHASE_RISE_END, 1.0))

            # Logo Y position: starts at bottom, rises to centre
            logo_y = _lerp(H * 0.85, cy, rise_t)
            logo_x = cx

            # Spin: full 360° rotation during rise
            spin_deg = rise_t * 360.0

            # Scale during zoom phase
            if t > _PHASE_CIRCLE_END:
                zoom_t = _ease_in_out((t - _PHASE_CIRCLE_END) / (_PHASE_ZOOM_END - _PHASE_CIRCLE_END))
                gem_scale = _lerp(1.0, max(W, H) / 40, zoom_t)
            else:
                gem_scale = 1.0

            base_gem_size = min(W, H) * 0.08  # gem half-size

            # ── Phase 2: Circle grows ────────────────────────────────
            circle_alpha = 0
            circle_radius = 0.0
            if t >= _PHASE_RISE_END:
                circ_t = _ease_out_cubic(min((t - _PHASE_RISE_END) / (_PHASE_CIRCLE_END - _PHASE_RISE_END), 1.0))
                circle_radius = _lerp(0, base_gem_size * 2.5, circ_t) * gem_scale
                circle_alpha = int(_lerp(0, 200, circ_t))

                # During zoom the alpha may increase
                if t > _PHASE_CIRCLE_END:
                    circle_alpha = 200

            # Transform for gem
            p.save()
            p.translate(logo_x, logo_y)
            p.rotate(spin_deg)
            p.scale(gem_scale, gem_scale)

            # Draw gem diamond
            dh = base_gem_size * 0.9
            dw = base_gem_size * 0.65

            gem = QPolygonF([
                QPointF(0, -dh),
                QPointF(dw, 0),
                QPointF(0, dh),
                QPointF(-dw, 0),
            ])

            gem_grad = QLinearGradient(-dw, -dh, dw, dh)
            gem_grad.setColorAt(0.0, QColor(c["accent"]))
            gem_grad.setColorAt(0.5, QColor(c["accent_hover"]))
            gem_grad.setColorAt(1.0, QColor(c["accent_muted"]))
            p.setBrush(QBrush(gem_grad))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(gem)

            # Facet lines
            facet_pen = QPen(QColor(255, 255, 255, 180), max(1.0, base_gem_size * 0.04))
            p.setPen(facet_pen)
            p.drawLine(QPointF(0, -dh), QPointF(dw, 0))
            p.drawLine(QPointF(0, -dh), QPointF(-dw * 0.55, -dh * 0.25))
            facet_pen.setColor(QColor(255, 255, 255, 70))
            p.setPen(facet_pen)
            p.drawLine(QPointF(-dw, 0), QPointF(dw, 0))

            p.restore()

            # Draw circle ring around logo position
            if circle_radius > 0 and circle_alpha > 0:
                ring_color = QColor(c["accent"])
                ring_color.setAlpha(circle_alpha)
                ring_pen = QPen(ring_color, max(2.0, base_gem_size * 0.08 * gem_scale))
                p.setPen(ring_pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(logo_x, logo_y), circle_radius, circle_radius)

                # Subtle glow
                glow = QColor(c["accent"])
                glow.setAlpha(max(0, circle_alpha // 3))
                glow_pen = QPen(glow, max(4.0, base_gem_size * 0.18 * gem_scale))
                p.setPen(glow_pen)
                p.drawEllipse(QPointF(logo_x, logo_y), circle_radius * 1.08, circle_radius * 1.08)

        # ── Version footer (visible until iris starts) ───────────────
        if t < _PHASE_ZOOM_END:
            footer = QColor(c["text_secondary"])
            footer.setAlpha(int(_lerp(0, 200, min(t / _PHASE_RISE_END, 1.0))))
            font = QFont()
            font.setPointSizeF(max(8.0, min(W, H) * 0.012))
            p.setFont(font)
            p.setPen(QPen(footer))
            p.drawText(
                QRectF(0, H - max(28.0, H * 0.06), W, 24.0),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                f"EfficientAssetRipper v{__version__}",
            )

        # ── Phase 4: Iris rectangle open ─────────────────────────────
        if t >= _PHASE_ZOOM_END:
            iris_t = _ease_in_out((t - _PHASE_ZOOM_END) / (_PHASE_IRIS_END - _PHASE_ZOOM_END))

            # Rectangle grows from zero at centre to full screen
            half_w = _lerp(0, W / 2, iris_t)
            half_h = _lerp(0, H / 2, iris_t)
            rect = QRectF(cx - half_w, cy - half_h, half_w * 2, half_h * 2)

            # Mask: paint the surrounding area (the "curtain")
            # We'll paint a solid bg everywhere, then clear the rect
            curtain_color = QColor(c["bg_darkest"])
            curtain_alpha = int(_lerp(255, 0, iris_t))
            curtain_color.setAlpha(curtain_alpha)

            # Draw the opening rectangle area as transparent (clear)
            # by painting a path that is full-screen minus the rect
            full = QPainterPath()
            full.addRect(0, 0, W, H)
            cutout = QPainterPath()
            cutout.addRoundedRect(rect, 10 * (1 - iris_t), 10 * (1 - iris_t))
            curtain_path = full - cutout

            p.fillPath(curtain_path, curtain_color)

            # Subtle border on the opening
            if iris_t < 0.95:
                border_color = QColor(c["accent"])
                border_color.setAlpha(int(_lerp(180, 0, iris_t)))
                p.setPen(QPen(border_color, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rect, 10 * (1 - iris_t), 10 * (1 - iris_t))

        p.end()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_animation_done(self):
        if not self._finished:
            self._finished = True
            try:
                self._finish_cb()
            finally:
                # Always close the splash, even if the callback raises;
                # leaving an invisible WindowStaysOnTop overlay around blocks
                # input on the main window.
                self.close()
