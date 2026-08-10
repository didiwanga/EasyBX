from __future__ import annotations

import re

from PyQt6.QtCore import QObject, QTimer

from xkxclient.automation.runner import ActionRunner

TEMPLATE_VAR_RE = re.compile(r"\{(\w+)(?::(\w+))?\}")


class Trigger:
    """B3 触发器：含模板匹配、计数、延时、动作、一次/多标签。"""

    def __init__(self, name: str, match_type: str = "contains", pattern: str = "",
                 conditions: list | None = None, actions: list | None = None,
                 delay_ms: int = 0, enabled: bool = True, one_shot: bool = False,
                 counter: int = 0, shared: bool = False, group: str = "",
                 relation: str = "or") -> None:
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
            self._tmpl_regex = re.compile(raw)
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
        self.pending = {}  # name -> QTimer
        self.master_on = True  # B9 全局开关

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
            )
            self.triggers.append(t)

    def handle_line(self, line: str) -> None:
        if not self.master_on:
            return
        for trg in self.triggers:
            if not trg.enabled:
                continue
            matched, captures = self._match_conditions(trg, line)
            if not matched:
                continue
            trg.counter += 1
            self._apply_captures(trg, captures)
            self.bus.publish("trigger.fired", account=self.session.account_id,
                             name=trg.name, line=line,
                             counter=trg.counter, captures=captures)
            self._schedule(trg)
            if trg.one_shot:
                trg.enabled = False

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
        """
        if c.get("match_type") == "status":
            ok = self._match_status_cond(c)
            return [] if ok else None
        mt = c.get("match_type") or t.match_type or "contains"
        pat = c.get("pattern") or ""
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

    def _apply_captures(self, t: Trigger, captures: list) -> None:
        """模板命名/编号变量写入 session.vars（B3 全局变量域）。"""
        if not captures:
            return
        names = list(getattr(t, "_names", []) or [])
        if names:
            for i, (var, _color) in enumerate(names):
                if i < len(captures):
                    self.session.vars[var] = captures[i]
        # 无命名模板（编号占据符结构）时按 v1..vN 写入
        named = [n for n, _ in (names or []) if n]
        if not named:
            for i, cap in enumerate(captures, 1):
                self.session.vars[f"v{i:02d}"] = cap

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
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda tt=t: self._fire_now(tt))
            timer.start(t.delay_ms)
            self.pending[t.name] = timer
        else:
            self._fire_now(t)

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
                    timer = self.pending.pop(t.name, None)
                    if timer:
                        timer.stop()

    def enable_all(self) -> None:
        for t in self.triggers:
            t.enabled = True

    def disable_all(self) -> None:
        for t in self.triggers:
            t.enabled = False

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