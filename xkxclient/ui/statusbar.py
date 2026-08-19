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
        self.login_label = QLabel()
        self.map_label = QLabel()
        self.sync_label = QLabel()

        self.addWidget(self.conn_label)
        self.addWidget(self.enc_label)
        self.addWidget(self.state_label, 1)
        self.addWidget(self.timer_label)
        self.addPermanentWidget(self.hint_label)
        self.addPermanentWidget(self.login_label)
        self.addPermanentWidget(self.map_label)
        self.addPermanentWidget(self.sync_label)
        self.set_login_status("未登录", False)
        self.set_map_server(False, "地图服务器未连接")
        self.set_sync_stats(None)

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

    @staticmethod
    def _dot(ok: bool) -> str:
        color = "#4caf50" if ok else "#9e9e9e"
        return f"<span style='color:{color};'>&#9679;</span>"

    @staticmethod
    def _tri_dot(state) -> str:
        """三态指示：True=绿(正常) False=红(失败) None=灰(未获取)。"""
        if state is True:
            color = "#4caf50"
        elif state is False:
            color = "#e53935"
        else:
            color = "#9e9e9e"
        return f"<span style='color:{color};'>&#9679;</span>"

    def set_login_status(self, text: str, ok: bool) -> None:
        self.login_label.setText(f"{self._dot(ok)} {text}")
        self.login_label.setToolTip("客户端账号登录状态" if ok else "客户端账号未登录")

    def set_map_server(self, ok: bool, text: str = "") -> None:
        if ok:
            t = "地图服务器已连接"
        else:
            t = "地图服务器连接失败"
        self.map_label.setText(f"{self._dot(ok)} {t}")
        self.map_label.setToolTip(text or t)

    def set_sync_stats(self, stats: dict | None) -> None:
        """地图同步统计实时显示：本地/服务器房间数、待上传、待下载、同步状态。"""
        if not stats:
            self.sync_label.setText(f"{self._tri_dot(None)} 地图同步统计…")
            self.sync_label.setToolTip("地图同步统计尚未获取")
            return
        server = stats.get("server_nodes")
        local = int(stats.get("local_nodes") or 0)
        up = int(stats.get("pending_upload") or 0)
        down = int(stats.get("pending_download") or 0)
        dot = self._tri_dot(stats.get("status_ok"))
        if server is None:
            self.sync_label.setText(f"{dot} 本地{local}·服务器获取中")
        else:
            self.sync_label.setText(f"{dot} 本地{local}·服务器{server}·待传{up}·待下{down}")
        self.sync_label.setToolTip(
            f"本地房间 {local} | 服务器房间 {server if server is not None else '-'} | "
            f"待上传 {up} | 待下载 {down} | 状态：{stats.get('status', '未知')}")