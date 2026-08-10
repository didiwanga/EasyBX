from __future__ import annotations

from PyQt6.QtCore import QObject

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

    def session(self, account_id: str):
        if account_id not in self._sessions:
            from xkxclient.net.session import AccountSession

            self._sessions[account_id] = AccountSession(self, account_id)
        return self._sessions[account_id]

    def shutdown(self) -> None:
        if self.shutting_down:
            return
        self.shutting_down = True
        for session in list(self._sessions.values()):
            session.close()
        self.config.save_all()