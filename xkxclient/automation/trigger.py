from __future__ import annotations

import math
import re
import time

from PyQt6.QtCore import QBuffer, QObject, QTimer
from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

from xkxclient.automation.runner import ActionRunner

TEMPLATE_VAR_RE = re.compile(r"\{(\w+)(?::(\w+))?\}")

_ding_players: list = []

# 高频触发保护：同一触发器在窗口内触发超限即自动停用（疑似死循环/错误配置），
# 避免命令疯狂囤积拖垮其他触发器与命令缓冲。
_HOT_WINDOW = 5.0        # 秒
_HOT_LIMIT = 30          # 窗口内触发次数上限


def play_ding() -> None:
    """播放一声合成「叮」（正弦衰减音，无需音频文件）。"""
    if _ding_players:
        return
    sample_rate = 44100
    duration = 0.4
    freq = 1046.5  # C6
    n = int(sample_rate * duration)
    raw = bytearray()
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-6.0 * t)
        v = max(-1.0, min(1.0, math.sin(2 * math.pi * freq * t) * env * 0.55))
        s = int(v * 32767)
        raw += s.to_bytes(2, "little", signed=True)
    fmt = QAudioFormat()
    fmt.setSampleRate(sample_rate)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    buf = QBuffer()
    buf.setData(bytes(raw))
    buf.open(QBuffer.OpenModeFlag.ReadOnly)
    sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt)
    sink.setVolume(0.9)
    sink.start(buf)
    holder = [buf, sink]
    _ding_players.append(holder)
    QTimer.singleShot(int(duration * 1000) + 200, lambda: _release(holder))


def _release(holder: list) -> None:
    if holder in _ding_players:
        _ding_players.remove(holder)


class Trigger:
    """B3 触发器：含模板匹配、计数、延时、动作、一次/多标签。"""

    def __init__(self, name: str, match_type: str = "contains", pattern: str = "",
                 conditions: list | None = None, actions: list | None = None,
                 delay_ms: int = 0, enabled: bool = True, one_shot: bool = False,
                 counter: int = 0, shared: bool = False, group: str = "",
                 relation: str = "or", beep: bool = False) -> None:
        self.name = name
        self.match_type = match_type
        self.pattern = pattern
        self.conditions = conditions or []
        self.relation = relation or "or"
        self.actions = actions or []
        self.delay_ms = delay_ms
        self.enabled = enabled
        self.one_shot = one_shot
        self.counter = counter
        self.shared = shared
        self.group = group
        self.beep = beep
        self._tmpl_regex = None

    @property
    def template_regex(self) -> re.Pattern | None:
        if self.match_type != "template":
            return None
        if self._tmpl_regex:
            return self._tmpl_regex
        parts, names = [], []
        idx = 0
        for m in TEMPLATE_VAR_RE.finditer(self.pattern):
            parts.append(re.escape(self.pattern[idx:m.start()]))
            names.append((m.group(1), m.group(2)))
            idx = m.end()
            parts.append("(.*?)")
        parts.append(re.escape(self.pattern[idx:]))
        raw = "".join(parts)
        # 至少一个变量才当模板；全静态退化为 contains
        if not names:
            return None
        try:
            self._tmpl_regex = re.compile(raw + "$")
            self._names = names
            return self._tmpl_regex
        except re.error:
            return None


class TriggerEngine(QObject):
    def __init__(self, bus, session) -> None:
        super().__init__(session)
        self.bus = bus
        self.session = session
        self.triggers: list[Trigger] = []
        self.runner = ActionRunner(bus, session)
        # 延时调度：单一共享 tick 定时器 + 到期时间表（monotonic）。
        # 不采用"每次命中新建一个 QTimer"：Windows 下 Qt 定时器 ID 复用
        # 会让新 QTimer 立即触发（qutebrowser #8191），长时运行后所有延时
        # 变成立即执行。单 tick + 时间表则无此问题。
        self.pending: dict[str, tuple[float, Trigger]] = {}  # name -> (到期monotonic, 触发器)
        self._delay_tick = QTimer(self)
        self._delay_tick.setInterval(50)
        self._delay_tick.timeout.connect(self._on_delay_tick)
        self.master_on = True  # B9 全局开关
        self._hot: dict[str, list[float]] = {}   # name -> 最近触发时刻（monotonic），高频保护用

    def load(self, definitions: list[dict]) -> None:
        self.triggers = []
        for d in definitions:
            dd = dict(d)
            t = Trigger(
                name=dd.get("name", ""),
                match_type=dd.get("match_type", "contains"),
                pattern=dd.get("pattern", ""),
                conditions=dd.get("conditions") or [],
                actions=dd.get("actions") or [],
                delay_ms=int(dd.get("delay_ms") or 0),
                enabled=bool(dd.get("enabled", True)),
                one_shot=bool(dd.get("one_shot", False)),
                counter=int(dd.get("counter") or 0),
                shared=bool(dd.get("shared", False)),
                group=dd.get("group", ""),
                relation=dd.get("relation", "or"),
                beep=bool(dd.get("beep", False)),
            )
            self.triggers.append(t)

    def handle_line(self, line: str, spans: list | None = None) -> list[str]:
        """处理一行文本，返回本行命中的触发器名列表（空 = 未命中）。

        spans 为带颜色分段的 Span 列表（可选）：模板捕获同时把捕获段颜色
        写入 `变量名:color` 变量（`{名:color}` 语法，供 substitute 读取）。
        """
        if not self.master_on:
            return []
        fired: list[str] = []
        for trg in self.triggers:
            if not trg.enabled:
                continue
            matched, captures = self._match_conditions(trg, line)
            if not matched:
                continue
            trg.counter += 1
            self._apply_captures(trg, captures, line, spans)
            self.bus.publish("trigger.fired", account=self.session.account_id,
                             name=trg.name, line=line,
                             counter=trg.counter, captures=captures)
            # 高频触发保护：超限自动停用，本行仅高亮不再执行步骤，避免拖累其他触发器
            if self._maybe_overheat(trg):
                fired.append(trg.name)
                continue
            if trg.beep:
                play_ding()
            self._schedule(trg)
            if trg.one_shot:
                trg.enabled = False
            fired.append(trg.name)
        return fired

    def _maybe_overheat(self, t: Trigger) -> bool:
        """高频触发检测：窗口内触发超限 → 自动停用并提示，返回 True（本行不再执行）。

        已停用状态直接返回 True（引擎不再重入提示），避免提示刷屏。
        """
        if not t.enabled:
            return True
        now = time.monotonic()
        q = self._hot.setdefault(t.name, [])
        q.append(now)
        self._hot[t.name] = [x for x in q if now - x <= _HOT_WINDOW]
        if len(self._hot[t.name]) > _HOT_LIMIT:
            t.enabled = False
            self.bus.publish("ui.message", account=self.session.account_id,
                             message=f"触发器「{t.name}」{_HOT_WINDOW:.0f}秒内触发超过 {_HOT_LIMIT} 次，已自动停用（疑似循环）")
            return True
        return False

    # ---- B3 多条件：全与(and) / 全或(or)，默认 or；模板变量捕获 ----
    def _match_conditions(self, t: Trigger, line: str) -> tuple[bool, list]:
        """返回 (是否命中, 捕获列表)。

        评估条件集 = 主 pattern（t.pattern）＋附加条件（t.conditions），按 t.relation 组合：
        - 主 pattern 始终参与（若其非空）。
        - 与(and)：全部命中，变量都赋值（各条件捕获合并）。
        - 或(or)：任一命中即触发；有多个命中时合并首个，模板捕获取首个命中条件的变量。
        """
        conds: list[dict] = []
        if t.pattern:
            conds.append({"match_type": t.match_type, "pattern": t.pattern})
        conds.extend(dict(c) for c in (t.conditions or []))
        if not conds:
            return False, []
        relation = t.relation if hasattr(t, "relation") else "or"
        if relation == "and":
            all_caps: list = []
            for c in conds:
                caps = self._eval_cond(c, t, line)
                if caps is None:
                    return False, []
                all_caps.extend(caps or [])
            return True, all_caps
        else:
            for c in conds:
                caps = self._eval_cond(c, t, line)
                if caps is not None:
                    # 或：仅取第一个命中条件的变量
                    return True, caps or []
            return False, []

    def _eval_cond(self, c: dict, t: Trigger, line: str) -> list | None:
        """评估单个条件：行匹配（contains/regex/exact/template）或状态比较（status）。

        命中返回捕获列表（可空），未命中返回 None。状态比较命中无捕获返回 []。
        pattern 支持 `{变量}` 与 `<保留变量>` 实时代入（如 `<中文名>说道：…`）。
        """
        if c.get("match_type") == "status":
            ok = self._match_status_cond(c)
            return [] if ok else None
        mt = c.get("match_type") or t.match_type or "contains"
        pat = c.get("pattern") or ""
        try:
            from xkxclient.automation.runner import substitute, substitute_template
            # 模板的 `{名}` 是捕获占位符，代入当前变量值会破坏捕获结构（命中一次后
            # 模板退化为静态文本）；模板只代入 `<保留变量>`，其余类型正常代入。
            if mt == "template":
                pat = substitute_template(pat, self.session.vars)
            else:
                pat = substitute(pat, self.session.vars)
        except Exception:
            pass
        return self._match_with(mt, pat, line)

    def _match_status_cond(self, c: dict) -> bool:
        """状态比较：GMCP 状态属性(attr) 与 比较值 判断（数值优先，字符串退化）。"""
        attr = c.get("attr") or "qi"
        op = c.get("op") or "="
        value = str(c.get("value") or "")
        current = getattr(self.session.state, attr, None)
        if current is None:
            return False
        try:
            cv, vv = float(current), float(value)
        except (TypeError, ValueError):
            cv, vv = str(current), str(value)
            if op in (">", "<", ">=", "<="):
                return False
            return (cv == vv) if op == "=" else (cv != vv)
        if op == ">":
            return cv > vv
        if op == "<":
            return cv < vv
        if op == ">=":
            return cv >= vv
        if op == "<=":
            return cv <= vv
        if op == "!=":
            return cv != vv
        return cv == vv

    def _match_with(self, match_type: str, pattern: str, line: str) -> list | None:
        tmp = Trigger("_c", match_type=match_type, pattern=pattern)
        return self._match(tmp, line)

    def _apply_captures(self, t: Trigger, captures: list, line: str | None = None,
                        spans: list | None = None) -> None:
        """模板命名/编号变量写入 session.vars（B3 全局变量域）。

        仅当捕获数量与主 pattern 模板变量数一致时按名字写入；不一致（and 多
        条件捕获合并、或 or 模式命中附加条件）时退化为 v01.. 编号写入，避免
        捕获错位写进错误变量。

        颜色捕获：模板声明 `{名:color}` 时（_color 非空），把捕获段前景色写入
        `名:color` 变量；编号退化情形总是写入 `vXX:color`。需要 spans 提供颜色。
        """
        from xkxclient.net.ansi import fg_at
        if not captures:
            return
        names = list(getattr(t, "_names", []) or [])
        if not names and getattr(t, "match_type", "") == "template":
            t.template_regex  # 访问 property 填充 _names（主 pattern 为模板时命名捕获生效）
            names = list(getattr(t, "_names", []) or [])
        if names and len(names) == len(captures):
            for i, (var, _color) in enumerate(names):
                self.session.vars[var] = captures[i]
                if _color and line is not None and spans:
                    fg = fg_at(spans, line, str(captures[i]))
                    if fg:
                        self.session.vars[f"{var}:color"] = fg
            return
        for i, cap in enumerate(captures, 1):
            self.session.vars[f"v{i:02d}"] = cap
            if line is not None and spans:
                fg = fg_at(spans, line, str(cap))
                if fg:
                    self.session.vars[f"v{i:02d}:color"] = fg

    def _match(self, t: Trigger, line: str) -> list | None:
        mt = t.match_type
        if mt == "contains":
            return [] if t.pattern in line else None
        if mt == "regex":
            try:
                m = re.search(t.pattern, line)
            except re.error:
                return None
            return list(m.groups()) if m else None
        if mt == "exact":
            return [] if line == t.pattern else None
        if mt == "template":
            rx = t.template_regex
            if rx is None:
                return [] if t.pattern in line else None
            m = rx.search(line)
            return list(m.groups()) if m else None
        return None

    # ---- 计数器 ----
    def count(self, name: str) -> int:
        for t in self.triggers:
            if t.name == name:
                return t.counter
        return 0

    def reset_counter(self, name: str) -> None:
        for t in self.triggers:
            if t.name == name:
                t.counter = 0

    def all_counts(self) -> dict[str, int]:
        return {t.name: t.counter for t in self.triggers}

    def _schedule(self, t: Trigger) -> None:
        if t.delay_ms > 0:
            # 同名单次命中的旧延时覆盖为新延时（语义同旧实现：先停旧再起新）
            self.pending[t.name] = (time.monotonic() + t.delay_ms / 1000.0, t)
            if not self._delay_tick.isActive():
                self._delay_tick.start()
        else:
            self._fire_now(t)

    def _on_delay_tick(self) -> None:
        now = time.monotonic()
        for name in [n for n, (fire_at, _t) in self.pending.items() if fire_at <= now]:
            item = self.pending.pop(name, None)
            if item is not None:
                self._fire_now(item[1])
        if not self.pending:
            self._delay_tick.stop()

    def _fire_now(self, t: Trigger) -> None:
        self.pending.pop(t.name, None)
        self.runner.run(t.actions)

    def enable(self, name: str) -> None:
        for t in self.triggers:
            if t.name == name:
                t.enabled = True

    def disable(self, name: str, stop: bool = False) -> None:
        for t in self.triggers:
            if t.name == name:
                t.enabled = False
                if stop:
                    self.pending.pop(t.name, None)
                    if not self.pending:
                        self._delay_tick.stop()

    def enable_all(self) -> None:
        for t in self.triggers:
            t.enabled = True

    def disable_all(self) -> None:
        for t in self.triggers:
            t.enabled = False
        # 全局停用：清空待执行延时、计数器、高频记录
        self._reset_state()

    def set_master(self, on: bool) -> None:
        """全局开关（UI 总开关调用）。

        停用时清空全部状态：待执行延时、已触发未执行的排队命令、计数器、高频记录；
        重新启用时以初始状态重新检测触发，避免错误操作/死循环大量囤积。
        """
        on = bool(on)
        if on == self.master_on:
            return
        self.master_on = on
        self._reset_state()
        if not on:
            thr = getattr(self.session, "throttle", None)
            if thr is not None:
                thr.cancel_all()

    def _reset_state(self) -> None:
        self.pending.clear()
        self._delay_tick.stop()
        self._hot.clear()
        for t in self.triggers:
            t.counter = 0

    def enable_group(self, group: str) -> None:
        for t in self.triggers:
            if (t.group or "") == group:
                t.enabled = True

    def disable_group(self, group: str) -> None:
        for t in self.triggers:
            if (t.group or "") == group:
                t.enabled = False

    def groups(self) -> list[str]:
        seen: list[str] = []
        for t in self.triggers:
            g = t.group or ""
            if g and g not in seen:
                seen.append(g)
        return seen