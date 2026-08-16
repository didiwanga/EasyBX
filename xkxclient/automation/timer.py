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
        td.enabled = pause
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
        if now < td.next_at:
            return
        td.next_at = now + 60
        if self._due(td, sched, now):
            if sched.get("type") == "once":
                td.enabled = False
            else:
                td.last_at = now
            self._fire(name)

    def _due(self, td: TimerDef, sched: dict, now: float) -> bool:
        import datetime as dt
        lt = dt.datetime.fromtimestamp(now)
        want = sched.get("daily_at")
        hm = (lt.hour, lt.minute)
        if want:
            # 兼容两种存储：
            # - 列表 ["09:30"]（旧 UI 格式）
            # - 分钟整数 570（引擎原生格式）
            raw = want[0] if isinstance(want, (list, tuple)) else want
            if isinstance(raw, str) and ":" in str(raw):
                hh, mm = str(raw).split(":", 1)
                target = (int(hh), int(mm))
            else:
                target = divmod(int(raw), 60)
            if hm == target:
                wd = sched.get("week_days") or []
                if wd and lt.weekday() + 1 not in wd:
                    return False
                if sched.get("type") == "once" and sched.get("once_at"):
                    once = sched.get("once_at")
                    if isinstance(once, str):
                        try:
                            once_ts = dt.datetime.strptime(once, "%Y-%m-%d %H:%M").timestamp()
                        except ValueError:
                            return False
                    else:
                        once_ts = float(once)
                    return abs(now - once_ts) < 120
                return True
            return False
        # once 无 daily_at：绝对时间比较
        if sched.get("type") == "once" and sched.get("once_at"):
            once = sched.get("once_at")
            if isinstance(once, str):
                try:
                    on_ts = dt.datetime.strptime(once, "%Y-%m-%d %H:%M").timestamp()
                except ValueError:
                    return False
            else:
                on_ts = float(once)
            return 0 <= (now - on_ts) < 60
        return False

    def _fire(self, name: str) -> None:
        if not self.master_on:
            return
        td = self.timers[name]
        td.last_at = time.time()
        self.bus.publish("timer.fired", account=self.session.account_id, name=name)
        self.runner.run(td.actions)
