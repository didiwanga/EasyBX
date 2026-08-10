from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QStatusBar


class StatusBar(QStatusBar):
    """状态栏（B7）：连接状态 / 编码 / 账号 / 人物状态 / 定时器数 / 右侧提示。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.conn_label = QLabel("未连接")
        self.enc_label = QLabel("gbk")
        self.state_label = QLabel("")
        self.timer_label = QLabel("")
        self.hint_label = QLabel("")

        self.addWidget(self.conn_label)
        self.addWidget(self.enc_label)
        self.addWidget(self.state_label, 1)
        self.addWidget(self.timer_label)
        self.addPermanentWidget(self.hint_label)

    def set_connection(self, text: str) -> None:
        self.conn_label.setText(text)

    def set_state(self, text: str) -> None:
        self.state_label.setText(text)

    def set_encoding(self, enc: str) -> None:
        self.enc_label.setText(enc)

    def set_timer_count(self, n: int) -> None:
        self.timer_label.setText(f"定时器 {n}" if n else "")

    def set_hint(self, text: str) -> None:
        self.hint_label.setText(text)