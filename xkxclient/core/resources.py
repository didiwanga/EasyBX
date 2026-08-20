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


def worldmap_ansi_bytes() -> bytes | None:
    """返回固化的北侠世界地图原始 ANSI 字节（含颜色码，GBK 编码）。

    数据固化在 materials/worldmap_ansi.bin，随客户端一起分发，
    所有用户无需抓包即可渲染富文本世界地图。
    """
    f = MATERIALS / "worldmap_ansi.bin"
    try:
        if not f.exists():
            return None
        return f.read_bytes()
    except OSError:
        return None