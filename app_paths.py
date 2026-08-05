"""
Resolve the application base directory.

When running from source, this is the directory containing this file.
When frozen with PyInstaller (--onedir or --onefile), this is the
temporary extraction directory stored in ``sys._MEIPASS``.
"""

import sys
from pathlib import Path


def app_dir() -> Path:
    """Return the root directory where bundled data files live."""
    if getattr(sys, "frozen", False):
        # Running inside a PyInstaller bundle
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).parent
