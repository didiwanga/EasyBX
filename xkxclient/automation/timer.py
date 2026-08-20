from __future__ import annotations

import time
from dataclasses import dataclass, field

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from xkxclient.automation.runner import ActionRunner


@dataclass
class TimerDef:
    name: str
    enabled: bool = True
    schedule: dict = field(default_factory=dict)  # {type, interval_ms, daily_at, week_days}
    actions: list = field(default_factory=list)
    last_at: float = 0.0
    next_at: float = 0.0
    group: str = ""


class TimerEngine(QObject):
    """E6 定时器（毫秒模型）：interval / daily/weekly / macron 依赖一次计时。"""

    # start/stop 可能来自脚本工作线程：经信号切回主线程再操作 QTimer
    _start_req = pyqtSignal(str)
    _stop_req = pyqtSignal(str, bool)

    def __init__(self, bus, session) -> None:
        super().__init__(session)
        self.bus = bus
        self.session = session
        self.timers: dict[str, TimerDef] = {}
        self.runner = ActionRunner(bus, session)
        self.master_on = True  # B9 全局开关
        self._paused: set[str] = set()   # 暂停的定时器（记住原状态，start 恢复）
        self._interval: dict[str, QTimer] = {}
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()
        self._start_req.connect(self._do_start)
        self._stop_req.connect(self._do_stop)

    def load(self, definitions: list[dict]) -> None:
        for t in self._interval.values():
            t.stop()
        self._interval.clear()
        fields = {"name", "enabled", "schedule", "actions", "last_at", "next_at", "group"}
        timers: dict[str, TimerDef] = {}
        for d in definitions:
            dd = {k: v for k, v in dict(d).items() if k in fields}
            try:
                td = TimerDef(**dd)
            except (TypeError, ValueError):
                continue  # 字段类型异常的历史脏数据：跳过，不崩溃
            timers[td.name] = td
        self.timers = timers
        # 清掉历史存留的 next_at（旧格式是滚动分钟值，可能已过期），
        # 首个 tick 依据 schedule 重新计算下一次触发时刻，避免加载即误触发。
        for td in self.timers.values():
            td.next_at = 0.0
        self._paused.clear()
        self._schedule_all()
        self._ensure_tick()

    def list(self) -> list[str]:
        return list(self.timers.keys())

    def _ensure_tick(self) -> None:
        if not self.timers and not self._tick.isActive():
            return
        if not self._tick.isActive():
            self._tick.start()

    def start(self, name: str) -> None:
        # 可能从脚本工作线程调用：信号切回主线程执行，避免在错误线程建 QTimer
        self._start_req.emit(str(name))

    def _do_start(self, name: str) -> None:
        if name not in self.timers:
            return
        td = self.timers[name]
        self.master_on = True  # 恢复全局开关：此前若被「控制→停止定时器」关闭，单台启动必须重新打开
        self._paused.discard(name)
        td.enabled = True
        sched = td.schedule or {}
        if sched.get("type") == "interval" or (sched.get("interval_ms") or 0) > 0:
            self._start_interval(name, td)
        self._ensure_tick()   # daily/weekly/once 依赖 tick 轮询
        self.bus.publish("timer.start", account=self.session.account_id, name=name)

    def stop(self, name: str, pause: bool = False) -> None:
        self._stop_req.emit(str(name), bool(pause))

    def _do_stop(self, name: str, pause: bool) -> None:
        if name not in self.timers:
            return
        td = self.timers[name]
        if pause:
            # 暂停：记住原状态并真正停用（daily/weekly 走 enabled 判断，必须禁用才不触发）
            self._paused.add(name)
        else:
            self._paused.discard(name)
        td.enabled = False
        if name in self._interval:
            self._interval[name].stop()
        if not pause:
            self.bus.publish("timer.stop", account=self.session.account_id, name=name)

    def toggle(self, name: str) -> None:
        if self.timers[name].enabled:
            self.stop(name)
        else:
            self.start(name)

    def stop_all(self) -> None:
        for t in self._interval.values():
            t.stop()
        self._interval.clear()
        self._paused.clear()
        for td in self.timers.values():
            td.next_at = 0.0
        self._tick.stop()

    def enable_all(self) -> None:
        """启用全部定时器（供触发器「控制→启动定时器」调用）。"""
        self.master_on = True
        self._paused.clear()
        for td in self.timers.values():
            td.enabled = True
        self._schedule_all()
        self._ensure_tick()

    def disable_all(self) -> None:
        """停用全部定时器（供触发器「控制→停止定时器」调用），
        停止所有 interval 计时并关闭全局开关，防止已排程定时器继续触发。"""
        self.master_on = False
        self._paused.clear()
        for t in self._interval.values():
            t.stop()
        self._interval.clear()
        for td in self.timers.values():
            td.next_at = 0.0
        self._tick.stop()

    def _schedule_all(self) -> None:
        for name, td in self.timers.items():
            sched = td.schedule or {}
            if not td.enabled:
                continue
            if sched.get("type") == "interval" or (sched.get("interval_ms") or 0) > 0:
                self._start_interval(name, td)

    def _start_interval(self, name: str, td: TimerDef) -> None:
        if name in self._interval and self._interval[name].isActive():
            return
        ms = (td.schedule or {}).get("interval_ms") or 1000
        t = QTimer(self)
        t.setInterval(max(1, ms))
        t.timeout.connect(lambda: self._fire(name))
        t.start()
        self._interval[name] = t

    def _on_tick(self) -> None:
        now = time.time()
        for name, td in self.timers.items():
            if not td.enabled:
                continue
            sched = td.schedule or {}
            stype = sched.get("type")
            if stype in ("daily", "weekly", "macron") or (not self._is_interval(sched)):
                self._check_calendar(name, td, sched, now)

    def _is_interval(self, sched: dict) -> bool:
        return "interval_ms" in sched and (sched.get("interval_ms") or 0) > 0

    def _check_calendar(self, name: str, td: TimerDef, sched: dict, now: float) -> None:
        """日历定时器到期检查：next_at 存精确触发时刻。

        - 一次性定时器（once_at）：绝对时间，仅在触发时刻后 120s 窗口内触发一次
          （加载过晚/过期不补触发）；
        - 每日/每周：首次 tick 由 daily_at 计算出下一次精确时刻；到期触发后推进到
          下一次。事件循环卡顿跨过目标分钟也能在恢复后的 tick 补触发，不再漏当天。
        """
        stype = sched.get("type")
        if not td.enabled:
            return
        once = sched.get("once_at")
        if once is not None:
            once_ts = self._parse_once(once)
            if once_ts is not None and 0 <= now - once_ts <= 120:
                td.enabled = False
                self._fire(name)
            return
        if td.next_at == 0.0:
            td.next_at = self._next_due(sched, now)
            if td.next_at is None:
                return
        if now < td.next_at:
            return
        td.last_at = now
        self._fire(name)
        if stype == "once":
            td.enabled = False
        else:
            td.next_at = self._next_due(sched, td.next_at)

    @staticmethod
    def _parse_once(once) -> float | None:
        """once_at 解析：`%Y-%m-%d %H:%M` 字符串或时间戳数值。"""
        import datetime as dt
        if isinstance(once, str):
            try:
                return dt.datetime.strptime(once, "%Y-%m-%d %H:%M").timestamp()
            except ValueError:
                return None
        try:
            return float(once)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _next_due(sched: dict, after: float) -> float | None:
        """after 之后（严格）下一次按 daily_at 触发的绝对时间戳；未配置返回 None。

        兼容两种存储：列表 ["09:30"]（旧 UI 格式）与分钟整数 570（引擎原生格式）；
        week_days 非空时按 1-7 过滤周一~周日。
        """
        import datetime as dt
        want = sched.get("daily_at")
        if not want:
            return None
        raw = want[0] if isinstance(want, (list, tuple)) else want
        if isinstance(raw, str) and ":" in str(raw):
            hh, mm = str(raw).split(":", 1)
            hh, mm = int(hh), int(mm)
        else:
            hh, mm = divmod(int(raw), 60)
        wd = sched.get("week_days") or []
        base = dt.datetime.fromtimestamp(after)
        for i in range(8):
            day = base + dt.timedelta(days=i)
            if wd and day.weekday() + 1 not in wd:
                continue
            ts = day.replace(hour=hh, minute=mm, second=0, microsecond=0).timestamp()
            if ts > after:
                return ts
        return None

    def _fire(self, name: str) -> None:
        if not self.master_on:
            return
        td = self.timers[name]
        td.last_at = time.time()
        self.bus.publish("timer.fired", account=self.session.account_id, name=name)
        self.runner.run(td.actions)
