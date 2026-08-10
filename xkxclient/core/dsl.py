from __future__ import annotations

import ast
import re

from xkxclient.automation.runner import substitute


class SysApi:
    """B3c `sys.*` 命名空间：会话内驱动。"""

    def __init__(self, session) -> None:
        self._s = session

    def send(self, cmd: str) -> None:
        self._s.send(cmd)

    def sendRaw(self, cmd: str) -> None:
        self._s.connection.send_line(cmd)

    def set_var(self, name: str, value) -> None:
        self._s.vars[name] = value

    def get_var(self, name: str, default=None):
        return self._s.vars.get(name, default)

    def echo(self, text) -> None:
        self._s.app.bus.publish("ui.message", account=self._s.account_id, message=text)

    def alert(self, text) -> None:
        self._s.app.bus.publish("ui.message", account=self._s.account_id, message=text)

    def say(self, text=None):
        return self._s.last_line if text is None else text


class DslEngine:
    """B3c MUD 函数 DSL：`## <表达式>` 求值器。

    在受限命名空间内执行（python eval/exec）：``sys.*`` / ``com.*`` 共享 / ``my.*`` 私有，
    控制流 if/for/while、比较与逻辑均可。`{}` 变量先代入。
    """

    def __init__(self, session) -> None:
        self.session = session
        self.com: dict = {}           # 跨会话共享（本进程内）
        self.my: dict = {}            # 本次调用私有

    @property
    def prefix(self) -> str:
        return "##"

    def is_command(self, text: str) -> bool:
        return text.startswith("##") and len(text) > 2 and text[2:3].isspace() or text == "##"

    def evaluate(self, text: str) -> tuple[bool, object]:
        """返回 (是否命中DSL, 结果)。未命中返回 (False, None)。"""
        if not self.is_command(text):
            return False, None
        expr = text[2:].strip()
        try:
            result = self._run(expr)
            return True, result
        except Exception as exc:  # 容错：DSL 错误不崩客户端
            return True, f"DSL 错误: {exc}"

    def _run(self, expr: str) -> object:
        expr = substitute(expr, self.session.vars)
        ns = {
            "sys": SysApi(self.session),
            "com": self.com,
            "my": self.my,
            "vars": self.session.vars,
        }
        # 语句流（多行 `;` 或 `|` 分隔），最后一行为值
        lines = [l.strip() for l in re.split(r"[;\n]", expr) if l.strip()]
        result = None
        for stmt in lines:
            result = eval_stmt(stmt, ns)
        return result


def _safe_globals():
    builtins_map = {}
    # 允许算术/比较/逻辑/容器，禁止 import/open/exec 等危险函数
    allowed = {"abs", "all", "any", "bool", "dict", "enumerate", "filter", "float",
               "int", "len", "list", "max", "min", "range", "reversed", "round",
               "set", "sorted", "str", "sum", "tuple", "zip", "map"}
    import builtins
    for name in allowed:
        if hasattr(builtins, name):
            builtins_map[name] = getattr(builtins, name)
    return {"__builtins__": builtins_map}

def eval_stmt(stmt: str, ns: dict):
    """求值单条语句：`if/else`、`for/while` 用 exec，表达式用 eval。"""
    g = _safe_globals()
    g.update(ns)
    if _is_stmt(stmt):
        exec(stmt, g, g)
        return None
    return eval(stmt, g, g)

def _is_stmt(stmt: str) -> bool:
    head = stmt.split(None, 1)[0] if stmt else ""
    return head in ("if", "else", "for", "while", "def", "return", "break", "continue", "import", "from")