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
        self._queue: list = []          # 元素: str=命令 | ("delay", ms)
        self._delay_until = 0.0         # 当前延时到期时间（monotonic）
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

    def enqueue_delay(self, ms: float) -> None:
        """排队一段延时：后续命令需等足该时长后才发送。"""
        ms = max(0.0, float(ms))
        if ms > 0:
            self._queue.append(("delay", ms))

    def enqueue_items(self, items: list) -> None:
        """排队一组动作序列：str=命令，("delay", ms)=延时，("beep", None)=提示音。"""
        for it in items:
            if isinstance(it, tuple) and len(it) == 2 and it[0] == "delay":
                self.enqueue_delay(it[1])
            elif isinstance(it, tuple) and len(it) >= 1 and it[0] == "beep":
                self._queue.append(("beep", None))
            else:
                self.enqueue(it)

    def cancel_all(self) -> None:
        self._queue.clear()
        self._delay_until = 0.0

    def set_gap(self, seconds: float) -> None:
        self.gap = max(0.0, float(seconds))

    def on_buffer_warning(self) -> None:
        """检测到服务端「命令进入缓冲」提示：立即限速并清空待发队列减压。"""
        self._panic_until = time.time() + 8.0
        self.gap = _PANIC_GAP
        # 服务端已因限流排队，把本地积压一并丢弃，避免雪上加霜
        self._queue = []
        self._delay_until = 0.0
        if self.bus:
            self.bus.publish("net.throttle", account=self.account,
                             status="命令进入缓冲，自动限频")

    # ---- 泵 ----
    def _pump(self) -> None:
        if not self._queue:
            self._busy = False
            return
        now = time.time()
        # 当前在延时等待中 → 时间未到则不发送
        if self._delay_until and now < self._delay_until:
            self._busy = True
            return
        self._delay_until = 0.0
        if self._sent and now - self._sent < self.gap:
            return
        # 恢复：缓冲告警结束后逐步回到安全间隔
        if self._panic_until and now > self._panic_until:
            self._panic_until = 0.0
            self.gap = _SAFE_GAP
        item = self._queue.pop(0)
        if isinstance(item, tuple) and item[0] == "delay":
            # 命中延时项：记录到期时间，后续命令等待
            self._delay_until = now + float(item[1]) / 1000.0
            self._busy = True
            return
        if isinstance(item, tuple) and item[0] == "beep":
            # 「叮」/beep 命令：播放提示音，不发往服务器
            self._busy = True
            from xkxclient.automation.trigger import play_ding
            play_ding()
            return
        self._busy = True
        self._sent = now
        self.connection.send_line(item)

    @property
    def busy(self) -> bool:
        return self._busy

    def pending(self) -> int:
        return len(self._queue)

    def close(self) -> None:
        self._timer.stop()

    def start(self) -> None:
        """连接恢复/重登后重启发送泵（close 停掉的 timer 不会自动恢复，
        否则该账号所有宏指令将积压队列永不发送，而手动直发不受影响）。"""
        if not self._timer.isActive():
            self._timer.start()