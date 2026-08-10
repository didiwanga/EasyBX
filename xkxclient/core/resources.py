from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtGui import QIcon, QPixmap

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    PROJECT_ROOT = Path(sys._MEIPASS)
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MATERIALS = PROJECT_ROOT / "materials"


def app_icon() -> QIcon | None:
    ico = MATERIALS / "app.ico"
    return QIcon(str(ico)) if ico.exists() else None


def app_logo() -> QPixmap | None:
    png = MATERIALS / "app.png"
    return QPixmap(str(png)) if png.exists() else None


def worldmap_pixmap() -> QPixmap | None:
    png = MATERIALS / "worldmap.png"
    return QPixmap(str(png)) if png.exists() else None