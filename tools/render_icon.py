"""Render the gem icon defined in main.py to a multi-resolution .ico file.

The runtime app builds the icon from QPixmap, which Pillow can save as
.ico with multiple sizes embedded. PyInstaller picks up the .ico via
``--icon`` so the EXE shows the gem in File Explorer instead of the
default wrench.

Usage (from repo root):

    py tools/render_icon.py [output.ico]

Default output is ``assets/icon.ico``. Run once after changing the icon
artwork; commit the resulting binary so contributors don't need PySide6
installed just to build a release.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Force a QApplication before importing main (needed for QPixmap)
from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from PIL import Image  # noqa: E402

_ICO_SIZES = (16, 32, 48, 64, 128, 256)


def _pixmap_to_pil(pixmap: QPixmap) -> Image.Image:
    """Round-trip a QPixmap through PNG bytes into a PIL Image."""
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "assets" / "icon.ico"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    from main import _make_icon
    qicon = _make_icon()

    images: list[Image.Image] = []
    for size in _ICO_SIZES:
        pix = qicon.pixmap(size, size)
        if pix.isNull():
            continue
        images.append(_pixmap_to_pil(pix))

    if not images:
        print("ERROR: no pixmaps rendered", file=sys.stderr)
        return 1

    base = max(images, key=lambda im: im.size[0])
    base.save(
        out_path,
        format="ICO",
        sizes=[im.size for im in images],
    )
    print(f"Wrote {out_path}  ({len(images)} sizes: {[im.size[0] for im in images]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
