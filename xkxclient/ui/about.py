from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QDialog

from xkxclient.core import resources
from xkxclient.version import VERSION


def version_string() -> str:
    return VERSION


class AboutDialog(QDialog):
    """关于对话框（R）：app.png 图标 + 版本信息。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("关于 EasyBXb")
        lay = QVBoxLayout(self)
        logo = resources.app_logo()
        if logo is not None:
            lbl = QLabel()
            lbl.setPixmap(logo.scaled(96, 96))
            lay.addWidget(lbl)
        lay.addWidget(QLabel("<b>EasyBXb</b>"))
        lay.addWidget(QLabel(f"版本 {VERSION}"))
        lay.addWidget(QLabel("PyQt6 MUD 客户端（设计见 wiki/）"))
        btn = QPushButton("确定")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)