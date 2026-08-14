from __future__ import annotations

import re
from typing import Callable

from PyQt6.QtCore import QObject, QTimer

from xkxclient.automation.runner import split_commands, substitute
from xkxclient.core.fullme import extract_fullme_url


class Macro:
    def __init__(self, name: str, enabled: bool = True, shared: bool = False, steps: list | None = None,
                 group: str = "") -> None:
        self.name = name
        self.enabled = enabled
        self.shared = shared
        self.steps = steps or []
        self.group = group
        self.labels = {s.get("label"): i for i, s in enumerate(self.steps) if s.get("label")}


class MacroEngine(QObject):
    """B3b 宏引擎：cmd/delay/label/jump(循环+条件)/if/input/wait_input/trigger/captcha + 暂停/恢复/进度。"""

    @staticmethod
    def _varname(name: str) -> str:
        """规范化变量名：去除前后空白、外层 `{}`、首部 `$`。`{v01}`/`v01`/`$v01` 等价。"""
        n = (name or "").strip()
        if n.startswith("$"):
            n = n[1:]
        return n.strip("{}").strip()

    def __init__(self, bus, session) -> None:
        super().__init__(session)
        self.bus = bus
        self.session = session
        self.macros: dict[str, Macro] = {}
        self._active: dict[str, Macro] = {}
        self._pos: dict[str, int] = {}
        self._timers: dict[str, QTimer] = {}
        self._loop_count: dict[str, int] = {}   # 计数循环：当前循环次数
        self._waiting: tuple[str, int] | None = None
        self._paused: set[str] = set()       # 被暂停的宏名
        self._wait_input_timer: QTimer | None = None
        self._trigger_sub: Callable | None = None
        self._trigger_timer: QTimer | None = None
        self._captcha_wait: tuple[str, int] | None = None   # (name, pos) 等待验证码输入
        self._captcha_sub: Callable | None = None           # net.text_display 订阅
        self._captcha_timer: QTimer | None = None           # 3s 检测窗口
        self._captcha_win = None                            # 验证码窗口引用（防 GC）
        self._call_stack: dict[str, list[int]] = {}         # 调用触发返回栈（每宏一个栈）

    def load(self, definitions: list[dict]) -> None:
        self.macros = {}
        for d in definitions:
            d = dict(d)
            name = d.pop("name")
            m = Macro(name, d.pop("enabled", True), d.pop("shared", False), d.pop("steps", []),
                      d.pop("group", ""))
            self.macros[m.name] = m
            # 运行中的宏：实时应用修改后的内容（不打断运行，保留当前步骤位置）
            if m.name in self._active:
                self._active[m.name] = m

    def list(self) -> list[str]:
        return list(self.macros.keys())

    def enable_all(self) -> None:
        for m in self.macros.values():
            m.enabled = True

    def disable_all(self) -> None:
        for m in self.macros.values():
            m.enabled = False

    def enable_group(self, group: str) -> None:
        for m in self.macros.values():
            if (m.group or "") == group:
                m.enabled = True

    def disable_group(self, group: str) -> None:
        for m in self.macros.values():
            if (m.group or "") == group:
                m.enabled = False

    def groups(self) -> list[str]:
        seen: list[str] = []
        for m in self.macros.values():
            g = m.group or ""
            if g and g not in seen:
                seen.append(g)
        return seen

    def start(self, name: str) -> bool:
        m = self.macros.get(name)
        if not m or not m.enabled or name in self._active:
            return False
        if self._active:   # B3b：单宏串行，一次只跑一个宏
            return False
        self._active[name] = m
        self._pos[name] = 0
        self._loop_count[name] = 0
        self._paused.discard(name)
        self.bus.publish("macro.start", account=self.session.account_id, name=name)
        self._step(name)
        return True

    def pause(self, name: str | None = None) -> None:
        """暂停当前宏（不指定则暂停全部运行中）。"""
        targets = [name] if name else list(self._active.keys())
        if not targets:
            return
        self._paused.update(t for t in targets if t in self._active)
        self._notify_state("paused")

    def resume(self, name: str | None = None) -> None:
        """恢复暂停的宏。"""
        targets = [name] if name else [t for t in self._active if t in self._paused]
        if not targets:
            return
        self._paused.difference_update(targets)
        self._notify_state("running")
        for t in targets:
            if t in self._active:
                self._step(t)

    def is_running(self, name: str | None = None) -> bool:
        if name:
            return name in self._active
        return bool(self._active)

    def is_paused(self, name: str | None = None) -> bool:
        if name:
            return name in self._paused
        return bool(self._paused)

    def progress(self, name: str) -> tuple[int, int]:
        """(当前步骤序号, 总步骤数)，1-based；未运行返回 (0, 总数)。"""
        m = self.macros.get(name)
        total = len(m.steps) if m else 0
        return (self._pos.get(name, 0) + 1, total)

    def stop(self) -> None:
        for t in self._timers.values():
            t.stop()
        self._timers.clear()
        self._clear_wait()
        names = list(self._active.keys())
        self._active.clear()
        self._pos.clear()
        self._loop_count.clear()
        self._paused.clear()
        self._call_stack.clear()
        self._waiting = None
        if names:
            self.bus.publish("macro.stop", account=self.session.account_id, names=names)

    def _clear_wait(self) -> None:
        if self._wait_input_timer is not None:
            self._wait_input_timer.stop()
            self._wait_input_timer = None
        if self._trigger_timer is not None:
            self._trigger_timer.stop()
            self._trigger_timer = None
        if self._trigger_sub is not None:
            self.bus.unsubscribe("net.text_display", self._trigger_sub)
            self._trigger_sub = None
        self._close_captcha()
        self._captcha_wait = None

    def _notify_state(self, state: str) -> None:
        self.bus.publish("macro.state", account=self.session.account_id, state=state)

    def _step(self, name: str) -> None:
        m = self._active.get(name)
        if not m:
            return
        if name in self._paused:
            return
        pos = self._pos.get(name, 0)
        if pos >= len(m.steps):
            self._halt(name)
            return
        step = m.steps[pos]
        t = step.get("type")

        self.bus.publish("macro.step", account=self.session.account_id,
                         name=name, index=pos + 1, total=len(m.steps))

        if t == "cmd":
            for cmd in split_commands(substitute(step.get("command", ""), self.session.vars)):
                self.session.send_auto(cmd)
            self._goto(name, pos + 1)
        elif t == "delay":
            self._chain(name, int(step.get("ms") or step.get("delay_ms") or 500), pos + 1)
        elif t == "label":
            self._goto(name, pos + 1)
        elif t == "jump":
            self._jump(name, step, pos)
        elif t == "if":
            self._if(name, step, pos)
        elif t == "status":
            self._status(name, step, pos)
        elif t == "input":
            self._wait_input(name, step, pos)
        elif t == "wait_input":
            self._wait_input(name, step, pos)
        elif t == "trigger":
            self._wait_trigger(name, step, pos)
        elif t == "call_trigger":
            # 调用触发：仅能被 call 步骤调用进入等待；顺序执行到此直接跳过
            if self._call_stack.get(name):
                self._wait_call_trigger(name, step, pos)
            else:
                self._goto(name, pos + 1)
        elif t == "call":
            self._call(name, step, pos)
        elif t == "hit":
            self._hit(name, step, pos)
        elif t == "move_trigger":
            self._move_trigger(name, step, pos)
        elif t == "captcha":
            self._wait_captcha(name, step, pos)
        elif t == "loop":
            self._loop(name, step, pos)
        elif t == "branch":
            self._branch(name, step, pos)
        else:
            self._goto(name, pos + 1)

    def _wait_input(self, name: str, step: dict, pos: int) -> None:
        """等待输入（B3b）：挂起宏，等待用户在输入框键入内容后继续。"""
        self._waiting = (name, pos)  # 记录等待点与最后 pos
        var = step.get("var") or "input"
        prompt = step.get("prompt") or ""
        timeout_ms = int(step.get("timeout_ms") or (step.get("timeout") or step.get("timeout_s") or 0) * 1000)
        self.bus.publish("macro.wait_input", account=self.session.account_id,
                         name=name, var=var, prompt=prompt)
        if timeout_ms > 0:
            self._wait_input_timer = QTimer(self)
            self._wait_input_timer.setSingleShot(True)
            self._wait_input_timer.timeout.connect(lambda: self._timeout_input(name, pos, var))
            self._wait_input_timer.start(timeout_ms)

    def _timeout_input(self, name: str, pos: int, var: str) -> None:
        """等待输入超时：变量保持未赋值，自动关闭继续向下。"""
        self._wait_input_timer = None
        if self._waiting == (name, pos):
            self._waiting = None
        if name in self._active:
            self._goto(name, pos + 1)

    def resume_input(self, text: str) -> None:
        """输入框发送时调用：若宏正等待输入，将文本写入变量并继续。"""
        if self._waiting is None:
            return
        name, pos = self._waiting
        self._waiting = None
        if self._wait_input_timer is not None:
            self._wait_input_timer.stop()
            self._wait_input_timer = None
        m = self._active.get(name)
        if not m or pos >= len(m.steps):
            return
        step = m.steps[pos]
        self.session.vars[self._varname(step.get("var") or "input")] = text
        self._goto(name, pos + 1)

    def _wait_trigger(self, name: str, step: dict, pos: int) -> None:
        """触发器步骤（B3b ⑦）：阻塞等待服务器条件命中并捕获变量。

        条件集 = conditions（B3 多条件，与/或）或旧单条件格式。
        """
        self._wait_trigger_impl(name, step, pos, pos + 1)

    def _wait_trigger_impl(self, name: str, step: dict, pos: int, return_pos: int) -> None:
        """触发器等待实现：条件命中/超时后跳转 return_pos（触发步骤=pos+1，调用触发=调用点）。"""
        from xkxclient.automation.trigger import Trigger, TEMPLATE_VAR_RE

        conds = step.get("conditions")
        if conds:
            conds = [dict(c) for c in conds]
            mt = conds[0].get("match_type", "contains")
            pat = conds[0].get("pattern", "")
            relation = step.get("relation", "or")
        else:
            cond = step.get("condition") or {}
            conds = [{"match_type": step.get("match_type") or cond.get("type") or "contains",
                      "pattern": step.get("pattern") or cond.get("pattern") or ""}]
            relation = "or"
        timeout_ms = int(step.get("timeout_ms") or (step.get("timeout") or step.get("timeout_s") or 0) * 1000)
        self.bus.publish("macro.state", account=self.session.account_id,
                         state="waiting_trigger", name=name)

        def eval_line(line: str) -> list | None:
            """按与/或评估条件集，返回捕获列表（None = 未命中）。"""
            if relation == "and":
                all_caps: list = []
                for c in conds:
                    caps = _match_trigger_cond(c, line)
                    if caps is None:
                        return None
                    all_caps.extend(caps or [])
                return all_caps
            for c in conds:
                caps = _match_trigger_cond(c, line)
                if caps is not None:
                    return caps or []
            return None

        def _match_trigger_cond(c: dict, line: str) -> list | None:
            mtx = c.get("match_type", "contains")
            if mtx == "status":
                return [] if self._match_status_cond(c) else None
            trg = Trigger("_c", match_type=mtx, pattern=c.get("pattern", ""))
            if mtx == "contains":
                return [] if trg.pattern in line else None
            if mtx == "exact":
                return [] if line == trg.pattern else None
            if mtx == "regex":
                try:
                    m = re.search(trg.pattern, line)
                except re.error:
                    return None
                return list(m.groups()) if m else None
            if mtx == "template":
                rx = trg.template_regex
                if rx is None:
                    return [] if trg.pattern in line else None
                m = rx.search(line)
                return list(m.groups()) if m else None
            return None

        def on_line(payload: dict) -> None:
            if name not in self._active or name in self._paused:
                return
            if (payload.get("account") or "") != self.session.account_id:
                return
            line = payload.get("line") or ""
            captures = eval_line(line)
            if captures is None:
                return
            # 命中：先真正取消订阅（否则后续行仍会回调，重复 goto/赋值）
            if self._trigger_sub is not None:
                self.bus.unsubscribe("net.text_display", self._trigger_sub)
                self._trigger_sub = None
            if self._trigger_timer is not None:
                self._trigger_timer.stop()
                self._trigger_timer = None
            # 模板变量捕获（B3：命名/编号都支持）
            names: list = []
            for c in conds:
                if c.get("match_type") == "template":
                    tmp = Trigger("_t", match_type="template", pattern=c.get("pattern", ""))
                    names.extend(getattr(tmp, "_names", []) or [])
            for i, (var, _color) in enumerate(names):
                g = captures[i] if i < len(captures) else ""
                var = self._varname(var)
                if var:
                    self.session.vars[var] = g
            if not names:
                for i, cap in enumerate(captures or [], 1):
                    self.session.vars[f"v{i:02d}"] = cap
            self._goto(name, return_pos)

        self._trigger_sub = self.bus.subscribe("net.text_display", on_line)
        if timeout_ms > 0:
            self._trigger_timer = QTimer(self)
            self._trigger_timer.setSingleShot(True)
            self._trigger_timer.timeout.connect(lambda: self._timeout_trigger(name, pos, return_pos))
            self._trigger_timer.start(timeout_ms)

    def _timeout_trigger(self, name: str, pos: int, return_pos: int | None = None) -> None:
        self._trigger_timer = None
        if self._trigger_sub is not None:
            self.bus.unsubscribe("net.text_display", self._trigger_sub)
            self._trigger_sub = None
        if name in self._active:
            self._goto(name, return_pos if return_pos is not None else pos + 1)

    def _wait_call_trigger(self, name: str, step: dict, pos: int) -> None:
        """调用触发步骤：与触发步骤相同，但命中/超时后跳转到调用点（栈顶返回位置）。"""
        stack = self._call_stack.get(name) or []
        return_pos = stack.pop() if stack else pos + 1
        self._wait_trigger_impl(name, step, pos, return_pos)

    def _call(self, name: str, step: dict, pos: int) -> None:
        """调用步骤：压入返回位置（pos+1），跳转到目标 call_trigger 标签。
        返回时 goto 调用行之后的步骤，避免重复执行调用行造成死循环。
        """
        target = step.get("target") or step.get("label") or ""
        m = self._active.get(name)
        if not m or not target:
            self._goto(name, pos + 1)
            return
        if target.isdigit():
            idx = max(0, int(target) - 1)
        else:
            idx = m.labels.get(target)
        if idx is None:
            self._goto(name, pos + 1)
            return
        self._call_stack.setdefault(name, []).append(pos + 1)
        self._goto(name, idx)

    def _hit(self, name: str, step: dict, pos: int) -> None:
        """等待命中步骤：先发送命令，等待触发条件命中。

        - 等待 delay_ms 内未命中 → 再次发送命令并继续等待（周期重发）
        - 条件命中 → 继续下一步
        - timeout_ms 超时（>0）→ 终止当前宏（默认 0 = 永不超时）
        """
        from xkxclient.automation.trigger import Trigger

        cmd = step.get("command", "")
        delay_ms = int(step.get("delay_ms") or 0)
        timeout_ms = int(step.get("timeout_ms") or (step.get("timeout") or step.get("timeout_s") or 0) * 1000)
        conds = step.get("conditions")
        if conds:
            conds = [dict(c) for c in conds]
            relation = step.get("relation", "or")
        else:
            cond = step.get("condition") or {}
            conds = [{"match_type": step.get("match_type") or cond.get("type") or "contains",
                      "pattern": step.get("pattern") or cond.get("pattern") or ""}]
            relation = "or"

        def eval_line(line: str) -> bool:
            if relation == "and":
                for c in conds:
                    if not self._hit_match(c, line):
                        return False
                return True
            return any(self._hit_match(c, line) for c in conds)

        def send_cmd() -> None:
            if cmd:
                for piece in split_commands(substitute(cmd, self.session.vars)):
                    self.session.send_auto(piece)

        self.bus.publish("macro.state", account=self.session.account_id,
                         state="waiting_trigger", name=name)
        send_cmd()

        done = [False]

        def finish(ok: bool) -> None:
            if done[0]:
                return
            done[0] = True
            if self._trigger_sub is not None:
                self.bus.unsubscribe("net.text_display", self._trigger_sub)
                self._trigger_sub = None
            if self._trigger_timer is not None:
                self._trigger_timer.stop()
                self._trigger_timer = None
            tm = self._timers.pop(name, None)
            if tm is not None:
                tm.stop()
            if name in self._active:
                if ok:
                    self._goto(name, pos + 1)
                else:
                    self._halt(name)

        def on_line(payload: dict) -> None:
            if done[0]:
                return
            if name not in self._active or name in self._paused:
                return
            if (payload.get("account") or "") != self.session.account_id:
                return
            if eval_line(payload.get("line") or ""):
                finish(True)

        self._trigger_sub = self.bus.subscribe("net.text_display", on_line)
        if timeout_ms > 0:
            self._trigger_timer = QTimer(self)
            self._trigger_timer.setSingleShot(True)
            self._trigger_timer.timeout.connect(lambda: finish(False))
            self._trigger_timer.start(timeout_ms)

        # 周期重发：每 delay_ms 未命中则重发命令
        if delay_ms > 0:
            from PyQt6.QtCore import QTimer as _QT
            tm = _QT(self)
            tm.setSingleShot(False)
            tm.timeout.connect(lambda: (send_cmd() if not done[0] else None))
            tm.start(delay_ms)
            self._timers[name] = tm

    def _hit_match(self, c: dict, line: str) -> bool:
        from xkxclient.automation.trigger import Trigger
        mtx = c.get("match_type", "contains")
        if mtx == "status":
            return self._match_status_cond(c)
        trg = Trigger("_c", match_type=mtx, pattern=c.get("pattern", ""))
        if mtx == "contains":
            return trg.pattern in line
        if mtx == "exact":
            return line == trg.pattern
        if mtx == "regex":
            try:
                return re.search(trg.pattern, line) is not None
            except re.error:
                return False
        if mtx == "template":
            rx = trg.template_regex
            if rx is None:
                return trg.pattern in line
            return rx.search(line) is not None
        return False

    def _move_trigger(self, name: str, step: dict, pos: int) -> None:
        """移动并触发步骤：`;` 分割的多个命令逐个发送，每个命令等待一次触发条件命中。

        - 发送当前命令 → 等待触发条件
        - 命中 → 延时 delay_ms 后发送下一个命令
        - 当前命令超时（timeout_ms，>0）未命中 → 跳过等待，直接发送下一个命令
        - 全部命令发送完 → 继续下一步
        """
        from xkxclient.automation.trigger import Trigger

        cmd = step.get("command", "")
        cmds = split_commands(substitute(cmd, self.session.vars))
        if not cmds:
            self._goto(name, pos + 1)
            return
        delay_ms = int(step.get("delay_ms") or 0)
        timeout_ms = int(step.get("timeout_ms") or (step.get("timeout") or step.get("timeout_s") or 0) * 1000)
        conds = step.get("conditions")
        if conds:
            conds = [dict(c) for c in conds]
            relation = step.get("relation", "or")
        else:
            cond = step.get("condition") or {}
            conds = [{"match_type": step.get("match_type") or cond.get("type") or "contains",
                      "pattern": step.get("pattern") or cond.get("pattern") or ""}]
            relation = "or"

        def eval_line(line: str) -> bool:
            if relation == "and":
                return all(self._hit_match(c, line) for c in conds)
            return any(self._hit_match(c, line) for c in conds)

        done = [False]

        def cleanup() -> None:
            if self._trigger_sub is not None:
                self.bus.unsubscribe("net.text_display", self._trigger_sub)
                self._trigger_sub = None
            if self._trigger_timer is not None:
                self._trigger_timer.stop()
                self._trigger_timer = None
            tm = self._timers.pop(name, None)
            if tm is not None:
                tm.stop()

        def send(idx: int) -> None:
            if done[0] or idx >= len(cmds):
                return
            self.session.send_auto(cmds[idx])
            wait(idx)

        def wait(idx: int) -> None:
            if done[0]:
                return
            self.bus.publish("macro.state", account=self.session.account_id,
                             state="waiting_trigger", name=name)
            waited = [False]

            def on_line(payload: dict) -> None:
                if done[0] or waited[0]:
                    return
                if name not in self._active or name in self._paused:
                    return
                if (payload.get("account") or "") != self.session.account_id:
                    return
                if eval_line(payload.get("line") or ""):
                    waited[0] = True
                    if self._trigger_sub is not None:
                        self.bus.unsubscribe("net.text_display", self._trigger_sub)
                        self._trigger_sub = None
                    if self._trigger_timer is not None:
                        self._trigger_timer.stop()
                        self._trigger_timer = None
                    if delay_ms > 0:
                        tm = QTimer(self)
                        tm.setSingleShot(True)
                        tm.timeout.connect(lambda: next_or_finish(idx + 1))
                        tm.start(delay_ms)
                        self._timers[name] = tm
                    else:
                        next_or_finish(idx + 1)

            def on_timeout() -> None:
                if done[0] or waited[0]:
                    return
                waited[0] = True
                if self._trigger_sub is not None:
                    self.bus.unsubscribe("net.text_display", self._trigger_sub)
                    self._trigger_sub = None
                if self._trigger_timer is not None:
                    self._trigger_timer.stop()
                    self._trigger_timer = None
                next_or_finish(idx + 1)

            self._trigger_sub = self.bus.subscribe("net.text_display", on_line)
            if timeout_ms > 0:
                self._trigger_timer = QTimer(self)
                self._trigger_timer.setSingleShot(True)
                self._trigger_timer.timeout.connect(on_timeout)
                self._trigger_timer.start(timeout_ms)

        def finish() -> None:
            if done[0]:
                return
            done[0] = True
            cleanup()
            if name in self._active:
                self._goto(name, pos + 1)

        def next_or_finish(idx: int) -> None:
            if idx >= len(cmds):
                finish()
            else:
                send(idx)

        self.bus.publish("macro.state", account=self.session.account_id,
                         state="waiting_trigger", name=name)
        send(0)

    # ---- 验证码步骤（新）：发送命令 → 3s 检测链接 → 弹窗输入 → 变量赋值 ----
    def _wait_captcha(self, name: str, step: dict, pos: int) -> None:
        """验证码步骤：先发送用户设置的命令，然后在 timeout 毫秒内监听 fullme 链接。

        - 命中链接：弹验证码窗（同 fullme 布局），用户输入回车/确定后把验证码
          写入 `var` 变量，宏继续下一步
        - 未命中：弹「未检测到验证码链接」对话框，确定后停止当前宏
        """
        timeout_ms = int(step.get("timeout_ms") or (step.get("timeout") or step.get("timeout_s") or 3) * 1000)
        var = step.get("var") or "captcha"
        # 1) 发送命令；并标记宏验证码期间抑制 session 普通 fullme 弹窗
        self.session._macro_captcha_active = True
        for cmd in split_commands(substitute(step.get("command", ""), self.session.vars)):
            self.session.send_auto(cmd)
        self.bus.publish("macro.state", account=self.session.account_id,
                         state="waiting_captcha", name=name)
        self._captcha_wait = (name, pos)

        def on_line(payload: dict) -> None:
            if self._captcha_wait != (name, pos):
                return
            if name not in self._active or name in self._paused:
                return
            if (payload.get("account") or "") != self.session.account_id:
                return
            url = extract_fullme_url(payload.get("line") or "")
            if not url:
                return
            # 2) 命中链接：先真正取消订阅（否则后续 URL 行会再弹窗），再弹窗
            if self._captcha_sub is not None:
                self.bus.unsubscribe("net.text_display", self._captcha_sub)
                self._captcha_sub = None
            if self._captcha_timer is not None:
                self._captcha_timer.stop()
                self._captcha_timer = None
            self._open_captcha_win(name, pos, var, url)

        self._captcha_sub = self.bus.subscribe("net.text_display", on_line)
        self._captcha_timer = QTimer(self)
        self._captcha_timer.setSingleShot(True)
        self._captcha_timer.timeout.connect(lambda: self._timeout_captcha(name, pos))
        self._captcha_timer.start(timeout_ms)

    def _open_captcha_win(self, name: str, pos: int, var: str, url: str) -> None:
        from xkxclient.ui.fullme import CaptchaWindow

        def on_submit(code: str) -> None:
            self.session.vars[self._varname(var)] = code
            self.session._macro_captcha_active = False
            self._captcha_win = None
            self._captcha_wait = None
            if name in self._active:
                self._goto(name, pos + 1)

        def on_finished(_result: int) -> None:
            # 未提交即关闭窗口：视为取消，停止宏
            if self._captcha_wait == (name, pos):
                self._captcha_wait = None
                self._captcha_win = None
                self.session._macro_captcha_active = False
                self.stop()

        self._captcha_win = CaptchaWindow(self.session, url, on_submit=on_submit)
        self._captcha_win.finished.connect(on_finished)
        self._captcha_win.show()

    def _timeout_captcha(self, name: str, pos: int) -> None:
        self._captcha_timer = None
        if self._captcha_sub is not None:
            self.bus.unsubscribe("net.text_display", self._captcha_sub)
            self._captcha_sub = None
        if self._captcha_wait != (name, pos):
            return
        self._captcha_wait = None
        self.session._macro_captcha_active = False
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(None, "验证码", "未检测到验证码链接，宏已停止。",
                                QMessageBox.StandardButton.Ok)
        self.stop()

    def _close_captcha(self) -> None:
        """停止/清理时关闭验证码监听与窗口。"""
        if self._captcha_timer is not None:
            self._captcha_timer.stop()
            self._captcha_timer = None
        if self._captcha_sub is not None:
            self.bus.unsubscribe("net.text_display", self._captcha_sub)
            self._captcha_sub = None
        if self._captcha_win is not None:
            self._captcha_win.close()
            self._captcha_win = None
        self._captcha_wait = None
        if getattr(self.session, "_macro_captcha_active", False):
            self.session._macro_captcha_active = False

    def _halt(self, name: str) -> None:
        self._active.pop(name, None)
        self._pos.pop(name, None)
        self._loop_count.pop(name, None)
        self._paused.discard(name)
        self._call_stack.pop(name, None)
        self._timers.pop(name, None)
        if self._trigger_sub is not None:
            self.bus.unsubscribe("net.text_display", self._trigger_sub)
            self._trigger_sub = None
        if self._trigger_timer is not None:
            self._trigger_timer.stop()
            self._trigger_timer = None
        if self._waiting and self._waiting[0] == name:
            self._waiting = None
        if self._captcha_wait and self._captcha_wait[0] == name:
            self._close_captcha()
        self.bus.publish("macro.end", account=self.session.account_id, name=name)

    def _chain(self, name: str, ms: int, next_pos: int) -> None:
        tm = QTimer(self)
        tm.setSingleShot(True)
        tm.timeout.connect(lambda: self._goto(name, next_pos))
        tm.start(ms)
        self._timers[name] = tm

    def _goto(self, name: str, target) -> None:
        m = self._active.get(name)
        if m and isinstance(target, str):
            if target.isdigit():
                # 步骤序号（1-based，来自 UI 下拉）→ 0-based 索引
                target = max(0, int(target) - 1)
            else:
                target = m.labels.get(target, 0)
        self._pos[name] = int(target)
        self._step(name)

    def _goto_later(self, name: str, target, ms: int = 0) -> None:
        """异步跳转：解析目标后经 QTimer 触发，避免同步递归（计数循环/跳转分支）。"""
        m = self._active.get(name)
        if m and isinstance(target, str):
            if target.isdigit():
                target = max(0, int(target) - 1)
            else:
                target = m.labels.get(target, 0)
        idx = int(target)
        self._chain(name, ms, idx)

    def _match(self, cond: dict) -> bool:
        ctype = cond.get("type") or cond.get("match_type")  # 兼容跳转(type) 与 判断条件列表(match_type)
        var = cond.get("var", "")
        if ctype in ("jump", "wait", "loop"):
            ok = self._last_line_has(cond.get("pattern", ""))
        elif ctype == "contains":
            ok = cond.get("pattern", "") in getattr(self.session, "last_line", "")
        elif ctype == "regex":
            ok = self._last_line_has(cond.get("pattern", ""))
        elif ctype == "cmp":
            key = self._varname(var)
            ok = self._cmp(str(self.session.vars.get(key, "")),
                           cond.get("op", "="), str(cond.get("value", "")))
        elif ctype == "status":
            ok = self._match_status_cond(cond)
        elif ctype == "true":
            ok = bool(self.session.vars.get(self._varname(var)))
        elif ctype == "not":
            ok = not bool(self.session.vars.get(self._varname(var)))
        elif ctype == "equals":
            ok = str(self.session.vars.get(self._varname(var))) == str(cond.get("value"))
        else:
            ok = False
        return (not ok) if cond.get("negate") else ok

    @staticmethod
    def _cmp(a: str, op: str, b: str) -> bool:
        try:
            fa, fb = float(a), float(b)
        except ValueError:
            fa = fb = None
        if fa is not None:
            if op == ">":
                return fa > fb
            if op == "<":
                return fa < fb
            if op == "!=":
                return fa != fb
            return fa == fb
        if op == ">":
            return a > b
        if op == "<":
            return a < b
        if op == "!=":
            return a != b
        return a == b

    def _jump(self, name: str, step: dict, pos: int) -> None:
        cond = step.get("condition")
        # 无条件跳转：无 condition 时直接跳转 then
        ok = self._match(cond) if cond else True
        if ok:
            target = step.get("then")
            if target is None:
                target = step.get("next") or (pos + 1)
            self._goto(name, target)
        else:
            self._goto(name, step.get("else") if step.get("else") is not None else pos + 1)

    def _if(self, name: str, step: dict, pos: int) -> None:
        # 多条件（B3b ⑤）：conditions 列表按 relation(与/或) 评估；单 condition 兼容旧数据
        conds = step.get("conditions")
        if conds:
            relation = step.get("relation", "or")
            if relation == "and":
                ok = all(self._match(c) for c in conds)
            else:
                ok = any(self._match(c) for c in conds)
        else:
            ok = self._match(step.get("condition", {}))
        delay_ms = int(step.get("delay_ms") or 0)
        timeout_ms = int(step.get("timeout_ms") or (step.get("timeout") or step.get("timeout_s") or 0) * 1000)
        branch_t = step.get("then")
        branch_f = step.get("else") if step.get("else") is not None else None
        if ok:
            if delay_ms > 0:
                self._branch_later(name, pos, branch_t, delay_ms)
            else:
                self._goto_branch(name, pos, branch_t)
        elif timeout_ms > 0:
            self._if_wait(name, step, pos, branch_t, branch_f, timeout_ms)
        else:
            self._goto_branch(name, pos, branch_f)

    def _if_wait(self, name: str, step: dict, pos: int, branch_t, branch_f, timeout_ms: int) -> None:
        """判断未命中且配置了超时：订阅新行/新状态，时限内重新评估；命中走真分支，超时走假分支。"""
        conds = step.get("conditions")
        if conds:
            relation = step.get("relation", "or")
        else:
            conds = [step.get("condition", {})]
            relation = "or"
        done = [False]

        def eval_again() -> bool:
            if relation == "and":
                return all(self._match(c) for c in conds)
            return any(self._match(c) for c in conds)

        def finish(ok: bool) -> None:
            if done[0]:
                return
            done[0] = True
            if self._trigger_sub is not None:
                self.bus.unsubscribe("net.text_display", self._trigger_sub)
                self._trigger_sub = None
            if self._trigger_timer is not None:
                self._trigger_timer.stop()
                self._trigger_timer = None
            if name not in self._active:
                return
            if ok:
                self._goto_branch(name, pos, branch_t)
            else:
                self._goto_branch(name, pos, branch_f)

        def on_line(payload: dict) -> None:
            if done[0]:
                return
            if name not in self._active or name in self._paused:
                return
            if (payload.get("account") or "") != self.session.account_id:
                return
            if eval_again():
                finish(True)

        self._trigger_sub = self.bus.subscribe("net.text_display", on_line)
        self._trigger_timer = QTimer(self)
        self._trigger_timer.setSingleShot(True)
        self._trigger_timer.timeout.connect(lambda: finish(False))
        self._trigger_timer.start(timeout_ms)

    def _branch_later(self, name: str, pos: int, branch, ms: int) -> None:
        """延时后执行判断/状态分支（含动作 + 去向）。"""
        tm = QTimer(self)
        tm.setSingleShot(True)

        def go():
            if name in self._active:
                self._goto_branch(name, pos, branch)
        tm.timeout.connect(go)
        tm.start(ms)
        self._timers[name] = tm

    def _goto_branch(self, name: str, pos: int, branch) -> None:
        """执行判断/状态分支：可选动作（cmd/set）+ 去向（标签/序号），缺省继续下一行。"""
        if branch is None:
            self._goto(name, pos + 1)
            return
        if isinstance(branch, dict):
            target = branch.get("target")
            action = branch.get("action")
            if action:
                self._exec_action(action)
        else:
            target = branch
        if target in (None, ""):
            target = pos + 1
        self._goto(name, target)

    def _loop(self, name: str, step: dict, pos: int) -> None:
        """计数循环步骤：以 `start`（步骤序号/标签）为循环起点，`count` 为次数。

        执行到本步时计数 +1：若尚未达到 count（count=0 视为无限），跳回起点继续；
        达到 count 后向下继续执行。
        """
        count = int(step.get("count") or 0)
        start = step.get("start") or ""
        cur = self._loop_count.get(name, 0) + 1
        self._loop_count[name] = cur
        if count <= 0 or cur < count:
            if start in (None, ""):
                self._goto(name, pos + 1)
            else:
                # 异步跳回起点：避免无限/高频循环同步递归爆栈
                self._goto_later(name, start)
        else:
            self._goto(name, pos + 1)

    def _branch(self, name: str, step: dict, pos: int) -> None:
        """判断分支步骤：等待触发条件命中（阻塞，同触发器步骤），从命中行搜寻关键字。

        - `conditions` 非空：等待条件命中（与/或）；为空时跳过条件判断，关键字即触发条件。
        - 多个关键字为「或」关系：按顺序取第一个在命中行出现的关键字执行对应动作。
        - 每个关键字动作：cmd(发送命令) / jump(跳转步骤/标签) / set(变量赋值)。
        - `delay_ms`：命中后延时执行动作；`timeout_ms`：等待超时，超时继续下一步。
        """
        conds = step.get("conditions")
        keywords = step.get("keywords") or []
        relation = step.get("relation", "or")
        delay_ms = int(step.get("delay_ms") or 0)
        timeout_ms = int(step.get("timeout_ms") or (step.get("timeout") or step.get("timeout_s") or 0) * 1000)

        def match_conds(line: str) -> bool:
            if not conds:
                return True
            if relation == "and":
                return all(self._match(c) for c in conds)
            return any(self._match(c) for c in conds)

        def find_keyword(line: str) -> dict | None:
            for kw in keywords:
                text = substitute(str(kw.get("keyword", "")), self.session.vars)
                if text and text in line:
                    return kw
            return None

        def on_line(payload: dict) -> None:
            if name not in self._active or name in self._paused:
                return
            if (payload.get("account") or "") != self.session.account_id:
                return
            line = payload.get("line") or ""
            if not match_conds(line):
                return
            kw = find_keyword(line)
            if kw is None:
                return
            # 命中：先取消订阅，避免后续行重复触发
            if self._trigger_sub is not None:
                self.bus.unsubscribe("net.text_display", self._trigger_sub)
                self._trigger_sub = None
            if self._trigger_timer is not None:
                self._trigger_timer.stop()
                self._trigger_timer = None
            action = kw.get("action") or {}
            if delay_ms > 0:
                tm = QTimer(self)
                tm.setSingleShot(True)
                tm.timeout.connect(lambda: self._exec_branch_action(name, action, pos))
                tm.start(delay_ms)
                self._timers[name] = tm
            else:
                self._exec_branch_action(name, action, pos)

        self._trigger_sub = self.bus.subscribe("net.text_display", on_line)
        if timeout_ms > 0:
            self._trigger_timer = QTimer(self)
            self._trigger_timer.setSingleShot(True)
            self._trigger_timer.timeout.connect(lambda: self._timeout_trigger(name, pos))
            self._trigger_timer.start(timeout_ms)

    def _exec_branch_action(self, name: str, action: dict, pos: int) -> None:
        """判断分支关键字动作：cmd / jump / set。jump 后不再继续本步骤链。"""
        t = action.get("type")
        if t == "cmd":
            for cmd in split_commands(substitute(action.get("command", ""), self.session.vars)):
                self.session.send_auto(cmd)
            self._goto(name, pos + 1)
        elif t == "jump":
            target = action.get("target")
            # 异步跳转：避免命中关键字后回跳本步造成同步递归
            self._goto_later(name, target if target not in (None, "") else pos + 1)
        elif t == "set":
            var = self._varname(action.get("var", ""))
            if var:
                self.session.vars[var] = substitute(str(action.get("value", "")), self.session.vars)
            self._goto(name, pos + 1)
        else:
            self._goto(name, pos + 1)

    def _status(self, name: str, step: dict, pos: int) -> None:
        """状态步骤（B3b 新增 ⑧）：判断 GMCP 状态属性是否满足比较条件。

        属性来自 session.state（气血/内力/精神/精力/食物/饮水/等级/经验/战意/真气/真元…）。
        条件形如 `attr op value`（value 可含 {变量}）；满足走 then 分支，否则走 else 分支。
        """
        attr = step.get("attr") or "qi"
        op = step.get("op") or "="
        value = substitute(str(step.get("value") or ""), self.session.vars)
        current = getattr(self.session.state, attr, None)
        ok = self._cmp_state(current, op, value)
        branch = step.get("then") if ok else (step.get("else") if step.get("else") is not None else None)
        self._goto_branch(name, pos, branch)

    def _cmp_state(self, current, op: str, value: str) -> bool:
        """状态值与比较值比较：优先数值，失败退化字符串。支持 =/≠/>/</≥/≤。"""
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

    def _match_status_cond(self, c: dict) -> bool:
        """状态比较条件（触发器步骤/判断步骤的 status 条件）：读 session.state 属性比较。"""
        attr = c.get("attr") or "qi"
        op = c.get("op") or "="
        value = substitute(str(c.get("value") or ""), self.session.vars)
        current = getattr(self.session.state, attr, None)
        return self._cmp_state(current, op, value)

    def _exec_action(self, action: dict) -> None:
        """判断分支动作：发送命令 / 变量赋值（B3b ⑤）。"""
        t = action.get("type")
        if t == "cmd":
            for cmd in split_commands(substitute(action.get("command", ""), self.session.vars)):
                self.session.send_auto(cmd)
        elif t == "set":
            var = self._varname(action.get("var", ""))
            val = action.get("value", "")
            if var:
                self.session.vars[var] = substitute(val, self.session.vars)

    def _last_line_has(self, pattern: str) -> bool:
        line = getattr(self.session, "last_line", "")
        try:
            return re.search(pattern, line) is not None
        except re.error:
            return False