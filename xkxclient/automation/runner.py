from __future__ import annotations

import re

_VAR_RE = re.compile(r"\{(\w+)(?::(\w+))?\}")
_DOLLAR_RE = re.compile(r"\$(\w+)")
# 保留变量调用语法：`<变量名>`（尖括号），与 `{变量}`/`$变量` 区分，
# 只能读取客户端自动获取的实时信息，不可被赋值覆盖。
_ANGLE_RE = re.compile(r"<([^<>]+)>")

# 客户端保留变量：名称 → 实时取值（从 session 读取）。仅客户端可维护，
# 用户只能通过 `<名>` 调用；任何赋值动作都会被 ReservedVars 拒绝。
RESERVED_VARS: dict[str, object] = {
    "中文名": lambda s: getattr(getattr(s, "state", None), "name", "") or "",
    "英文名": lambda s: getattr(getattr(s, "state", None), "id", "") or "",
    "门派": lambda s: getattr(getattr(s, "state", None), "family", "") or "",
    "级别": lambda s: getattr(getattr(s, "state", None), "level", "") or "",
    "经验": lambda s: getattr(getattr(s, "state", None), "combat_exp", "") or "",
    "气血": lambda s: getattr(getattr(s, "state", None), "qi", "") or "",
    "精神": lambda s: getattr(getattr(s, "state", None), "jing", "") or "",
    "精力": lambda s: getattr(getattr(s, "state", None), "jingli", "") or "",
    "内力": lambda s: getattr(getattr(s, "state", None), "neili", "") or "",
    "食物": lambda s: getattr(getattr(s, "state", None), "food", "") or "",
    "饮水": lambda s: getattr(getattr(s, "state", None), "water", "") or "",
    "战意": lambda s: getattr(getattr(s, "state", None), "fighter_spirit", "") or "",
    "真气": lambda s: getattr(getattr(s, "state", None), "vigour", "") or "",
    "真元": lambda s: getattr(getattr(s, "state", None), "yuan", "") or "",
    "房间名": lambda s: getattr(s, "room_name", "") or "",
}


class ReservedVars(dict):
    """会话变量字典：拒绝给客户端保留变量主动赋值（仅能读取）。

    所有宏/触发器/等待输入等对 `session.vars` 的写入都经过本类，
    保留变量名（如 `<中文名>`）一旦被赋值即被拦截并提示。
    """

    def __init__(self, session, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._session = session

    def __setitem__(self, key, value) -> None:
        if key in RESERVED_VARS:
            bus = getattr(getattr(self._session, "app", None), "bus", None)
            if bus is not None:
                try:
                    bus.publish(
                        "ui.message",
                        account=getattr(self._session, "account_id", ""),
                        message=f"「{key}」是客户端保留变量，只能调用不能赋值",
                    )
                except Exception:
                    pass
            return
        super().__setitem__(key, value)

    def update(self, *args, **kwargs) -> None:
        for k, v in dict(*args, **kwargs).items():
            self[k] = v


def _reserved_value(session, name: str):
    getter = RESERVED_VARS.get(name)
    if getter is None:
        return None
    try:
        return getter(session)
    except Exception:
        return None


def _substitute_reserved(text: str, vars: dict) -> str:
    """仅代入 `<保留变量>`（尖括号），不动 `{变量}`/`$变量`。"""
    def rep_angle(m):
        name = m.group(1).strip()
        if name in RESERVED_VARS:
            sess = getattr(vars, "_session", None)
            if sess is not None:
                val = _reserved_value(sess, name)
                if val not in (None, ""):
                    return str(val)
        return m.group(0)
    return _ANGLE_RE.sub(rep_angle, text)


def substitute_template(text: str, vars: dict) -> str:
    """模板 pattern 的实时代入：仅替换 `<保留变量>`。

    模板的 `{名}`/`{名:color}` 是捕获占位符（由 template_regex 编译为捕获组），
    若用 substitute 把 `{变量}` 也替换成当前值，命中一次后 vars 里出现同名键
    （如捕获的 `名`），模板就会退化为静态文本，其它行不再匹配。
    """
    return _substitute_reserved(text, vars)


def substitute(text: str, vars: dict) -> str:
    """`{变量}` / `$变量` / `<保留变量>` 代入（wiki B3/B3b）。

    - `{v01}` → vars['v01']；`{v01:color}` → vars['v01:color']（颜色 #RRGGBB）；
    - `$v01` → vars['v01']（宏验证码步骤产物，兼容 `$` 调用）；
    - `<中文名>` 等尖括号 → 客户端保留变量实时值（如 say <中文名>），
      未定义/无值时保留原占位符。
    """
    out = _substitute_reserved(text, vars)

    def rep(m):
        key = m.group(1)
        if m.group(2):
            key = f"{key}:{m.group(2)}"
        return str(vars.get(key, m.group(0)))
    out = _VAR_RE.sub(rep, out)

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
        if target == "macro":
            self._control_macro(op)
            return
        engines = {
            "trigger": self.session.triggers,
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

    def _control_macro(self, op: str) -> None:
        """宏控制走 MacroEngine 真正的方法：
        - stop：停止所有运行中的宏（仅 disable_all 只标记禁用，停不掉已在运行的宏）
        - pause/resume：暂停/恢复运行中的宏
        - start：启用所有宏定义（允许被启动；实际启动仍需指定宏名，单宏串行）
        """
        eng = self.session.macros
        if op == "stop":
            eng.stop()
        elif op == "pause":
            eng.pause()
        elif op == "resume":
            eng.resume()
        else:  # start
            eng.enable_all()