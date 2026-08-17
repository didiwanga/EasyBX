from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox, QLabel, QPushButton, QVBoxLayout, QDialog

from xkxclient.core import resources
from xkxclient.net import connection
from xkxclient.version import VERSION


def version_string() -> str:
    return VERSION


class AboutDialog(QDialog):
    """关于对话框（R）：app.png 图标 + 版本信息 + 开发者 Debug 开关。"""

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
        self.dump_cb = QCheckBox("Debug（开发者调试原始数据用，无需开启）")
        self.dump_cb.setChecked(connection.DEBUG_DUMP)
        self.dump_cb.toggled.connect(self._on_dump_toggled)
        lay.addWidget(self.dump_cb)
        self.dump_hint = QLabel("")
        self.dump_hint.setWordWrap(True)
        lay.addWidget(self.dump_hint)
        btn = QPushButton("确定")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)

    def _on_dump_toggled(self, on: bool) -> None:
        connection.DEBUG_DUMP = on
        if on:
            self.dump_hint.setText(
                "原始字节将追加写入：\n"
                f"{connection.debug_dump_path()}\n"
                "复现问题后请取消勾选并关闭本窗口，把该文件发我。"
            )
        else:
            self.dump_hint.setText("")