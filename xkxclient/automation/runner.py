from __future__ import annotations

import re

_VAR_RE = re.compile(r"\{(\w+)(?::(\w+))?\}")
_DOLLAR_RE = re.compile(r"\$(\w+)")


def substitute(text: str, vars: dict) -> str:
    """`{变量}` / `$变量` 代入（wiki B3，统一 `{}` 格式）；`{名:color}` 取颜色值。

    `{v01}` → vars['v01']；`{v01:color}` → vars['v01:color']（颜色 #RRGGBB），
    `$v01` → vars['v01']（宏验证码步骤产物，兼容 `$` 调用）；
    未定义时保留原占位符。
    """
    def rep(m):
        key = m.group(1)
        if m.group(2):
            key = f"{key}:{m.group(2)}"
        return str(vars.get(key, m.group(0)))
    out = _VAR_RE.sub(rep, text)

    def rep2(m):
        key = m.group(1)
        return str(vars.get(key, m.group(0)))
    return _DOLLAR_RE.sub(rep2, out)


def split_commands(text: str) -> list[str]:
    """`;` 拆分多命令（wiki B6/B3b）。"""
    return [c.strip() for c in text.split(";") if c.strip()]


class ActionRunner:
    """统一动作器（wiki B3/E6/B3b）：输出命令 / 启动停止定时器 / 通知 / 控制。"""

    def __init__(self, bus, session) -> None:
        self.bus = bus
        self.session = session

    def run(self, actions: list[dict], vars: dict | None = None) -> None:
        vars = vars if vars is not None else getattr(self.session, "vars", {})
        for a in actions:
            t = a.get("type")
            if t == "cmd":
                for cmd in split_commands(substitute(a.get("command", ""), vars)):
                    self.session.send_auto(cmd)
            elif t == "timer_start":
                self.session.timers.start(a.get("name", ""))
            elif t == "timer_stop":
                self.session.timers.stop(a.get("name", ""))
            elif t == "notify":
                self.bus.publish("ui.message", account=self.session.account_id,
                                 message=substitute(a.get("message", ""), vars))
            elif t == "control":
                target = a.get("target", "")
                op = a.get("op", "start")
                self._control(target, op)

    def _control(self, target: str, op: str) -> None:
        engines = {
            "trigger": self.session.triggers,
            "macro": self.session.macros,
            "timer": self.session.timers,
        }
        eng = engines.get(target)
        if eng is None:
            return
        # start/stop/resume=启用全部，pause/stop=停用全部；引擎需有 enable_all/disable_all。
        if op in ("start", "resume"):
            if hasattr(eng, "enable_all"):
                eng.enable_all()
            elif hasattr(eng, "enable_group") and hasattr(eng, "groups"):
                for g in eng.groups():
                    eng.enable_group(g)
        elif op in ("stop", "pause"):
            if hasattr(eng, "disable_all"):
                eng.disable_all()
            elif hasattr(eng, "disable_group") and hasattr(eng, "groups"):
                for g in eng.groups():
                    eng.disable_group(g)