from __future__ import annotations

import traceback

from PyQt6.QtCore import QEventLoop, QObject, QTimer
from PyQt6.QtNetwork import QAbstractSocket

from xkxclient.core.bus import EventBus
from xkxclient.core.config import ConfigManager


class XkxApp(QObject):
    """核心装配（wiki E8-启动流程.md）：总线 + 配置 + 账号会话（D4 每标签独立）。"""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bus = EventBus(self)
        self.config = ConfigManager.instance()
        self.config.bus = self.bus
        self._sessions: dict[str, object] = {}
        self.shutting_down = False
        self._scripts = None
        self.bus.subscribe("login.done", self._on_login_done)

    def scripts(self):
        """懒装配脚本引擎（E8-脚本API细化），首次登录前不初始化。"""
        if self._scripts is None:
            from xkxclient.scripting.script_engine import ScriptManager

            self._scripts = ScriptManager(self, self)
        return self._scripts

    def session(self, account_id: str):
        if account_id not in self._sessions:
            from xkxclient.net.session import AccountSession

            self._sessions[account_id] = AccountSession(self, account_id)
        return self._sessions[account_id]

    def _on_login_done(self, payload: dict) -> None:
        account = payload.get("account")
        if not account:
            return
        try:
            self.scripts().run_enabled(account)
        except Exception:
            traceback.print_exc()

    def shutdown(self) -> None:
        if self.shutting_down:
            return
        self.shutting_down = True
        if self._scripts is not None:
            try:
                self._scripts.shutdown()
            except Exception:
                traceback.print_exc()
        self._graceful_logout()
        for session in list(self._sessions.values()):
            session.close()
        self.config.save_all()

    def _graceful_logout(self) -> None:
        """关闭前优雅登出：给每个已登录账号发 quit，等服务端断开（最多 3s）。

        QTcpSocket 为异步收发，需在关闭 socket 前留出事件循环时间让
        服务器处理 quit（存档/物品落袋），避免直接断线丢物品。
        """
        sessions = [s for s in self._sessions.values()
                    if getattr(s, "logged_in", False) and getattr(s, "connected", False)]
        if not sessions:
            return
        for s in sessions:
            try:
                s.logout()
            except Exception:
                traceback.print_exc()
        self._wait_sockets_closed(sessions, timeout_ms=3000)

    def _wait_sockets_closed(self, sessions, timeout_ms: int = 3000) -> None:
        """等待事件循环里所有 session socket 变为 Unconnected，最多 timeout_ms。"""
        deadline = QTimer(self)
        deadline.setSingleShot(True)
        loop = QEventLoop(self)
        deadline.timeout.connect(loop.quit)

        def check() -> None:
            for s in sessions:
                sock = getattr(getattr(s, "connection", None), "sock", None)
                if sock is None:
                    continue
                if sock.state() != QAbstractSocket.SocketState.UnconnectedState:
                    return
            loop.quit()

        poll = QTimer(self)
        poll.setInterval(100)
        poll.timeout.connect(check)
        deadline.start(timeout_ms)
        poll.start()
        loop.exec()
        poll.stop()