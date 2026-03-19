"""Resolve the application base directory (works for both source and frozen exe)."""

import sys
from pathlib import Path


def base_dir() -> Path:
    """Return the project root directory."""
    if getattr(sys, "frozen", False):
        # PyInstaller --onedir: exe sits in the dist folder
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent
