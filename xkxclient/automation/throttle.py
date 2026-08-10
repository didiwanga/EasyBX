from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QTimer

from xkxclient.core.config import ConfigManager

# 服务端命令缓冲上限参考（wiki about_cmdbuffer）：高峰期约 30 条/秒，超限进缓冲。
_SAFE_GAP = 0.08       # 常态最小间隔≈80ms（≤12 条/秒，留足余量）
_PANIC_GAP = 0.5       # 收到「命令进入缓冲」提示后的保守间隔


class CommandThrottle(QObject):
    """全局命令发送节流（wiki about_cmdbuffer 命令缓冲）。

    自动引擎（触发器/宏/战斗轮转）生成的命令经本队列：
    - 常规状态按 _SAFE_GAP 限频发送，避免冲击服务器命令缓冲；
    - 收到「命令进入缓冲」提示后进入限速状态，间隔放大，随后指数恢复；
    - 供脚本体感输入行的命令不经队列（交互式命令仍即时发送）。
    """

    def __init__(self, connection, account: str, bus=None, parent=None) -> None:
        super().__init__(parent)
        self.connection = connection
        self.account = account
        self.bus = bus
        self.gap = _SAFE_GAP
        self._queue: list[str] = []
        self._panic_until = 0.0
        self._busy = False
        self._sent = 0
        cfg = ConfigManager.instance()
        self.gap = max(0.0, float(cfg.get("net.cmd_gap", _SAFE_GAP)))

        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._pump)
        self._timer.start()

    # ---- 对外 ----
    def enqueue(self, text: str) -> None:
        """排队一条待发送命令（自动化发命令走这里）。"""
        if not text or text.strip() == "":
            return
        self._queue.append(text)

    def cancel_all(self) -> None:
        self._queue.clear()

    def set_gap(self, seconds: float) -> None:
        self.gap = max(0.0, float(seconds))

    def on_buffer_warning(self) -> None:
        """检测到服务端「命令进入缓冲」提示：立即限速并清空待发队列减压。"""
        self._panic_until = time.time() + 8.0
        self.gap = _PANIC_GAP
        # 服务端已因限流排队，把本地积压一并丢弃，避免雪上加霜
        self._queue = []
        if self.bus:
            self.bus.publish("net.throttle", account=self.account,
                             status="命令进入缓冲，自动限频")

    # ---- 泵 ----
    def _pump(self) -> None:
        if not self._queue:
            self._busy = False
            return
        if self._sent and time.time() - self._sent < self.gap:
            return
        # 恢复：缓冲告警结束后逐步回到安全间隔
        if self._panic_until and time.time() > self._panic_until:
            self._panic_until = 0.0
            self.gap = _SAFE_GAP
        text = self._queue.pop(0)
        self._busy = True
        self._sent = time.time()
        self.connection.send_line(text)

    @property
    def busy(self) -> bool:
        return self._busy

    def pending(self) -> int:
        return len(self._queue)

    def close(self) -> None:
        self._timer.stop()