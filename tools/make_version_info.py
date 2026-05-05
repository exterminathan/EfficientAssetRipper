"""Generate a PyInstaller VERSIONINFO file from `_version.__version__`.

Run from the repo root:

    py tools/make_version_info.py [output_path]

If `output_path` is omitted, writes to `build/version_info.txt`. Called from
`build.bat` before PyInstaller so the EXE's Windows file properties
(Properties → Details) carry the same version surfaced in the UI.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from _version import __version__  # noqa: E402


def _semver_tuple(version: str) -> tuple[int, int, int, int]:
    """Return a Windows-VERSIONINFO four-tuple from a semver string."""
    base = version.split("-", 1)[0]
    parts = re.findall(r"\d+", base)
    nums = [int(p) for p in parts[:3]] + [0] * (3 - len(parts[:3]))
    nums.append(0)  # build number — unused
    return tuple(nums)  # type: ignore[return-value]


_TEMPLATE = """\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={ver_tuple},
    prodvers={ver_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'040904B0',
          [
            StringStruct(u'CompanyName', u'EfficientAssetRipper'),
            StringStruct(u'FileDescription', u'EfficientAssetRipper - Unpack UE4/5 game files and export to Blender'),
            StringStruct(u'FileVersion', u'{ver_str}'),
            StringStruct(u'InternalName', u'EfficientAssetRipper'),
            StringStruct(u'LegalCopyright', u'MIT License'),
            StringStruct(u'OriginalFilename', u'EfficientAssetRipper.exe'),
            StringStruct(u'ProductName', u'EfficientAssetRipper'),
            StringStruct(u'ProductVersion', u'{ver_str}'),
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "build" / "version_info.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        _TEMPLATE.format(ver_tuple=_semver_tuple(__version__), ver_str=__version__),
        encoding="utf-8",
    )
    print(f"Wrote {out_path}  (version {__version__})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
