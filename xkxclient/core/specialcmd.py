"""全域特殊命令解析：#wa N（延时）与 #N cmd（重复发送）。

在 session.send / send_auto 的统一入口解析，覆盖命令框、触发器、别名、
定时器、宏等所有发送命令的路径。解析产物为动作序列：
  ("cmd", text)     发送命令
  ("delay", ms)     等待 ms 毫秒后继续后续动作
"""

from __future__ import annotations

import re

_WA_RE = re.compile(r"^#wa\s*(\d+)\s*$", re.IGNORECASE)
_REPEAT_RE = re.compile(r"^#(\d+)\s+(.+)$")
# 单条重复/延时的安全上限，避免误输入导致客户端长时间卡在队列里
_MAX_REPEAT = 500
_MAX_DELAY_MS = 600_000      # 10 分钟


def is_special(text: str) -> bool:
    """text 是否命中任意特殊命令。"""
    t = (text or "").strip()
    return bool(_WA_RE.match(t) or _REPEAT_RE.match(t))


def build_items(pieces: list[str]) -> list:
    """把已按 `;` 拆分的命令串转为动作序列。

    返回顺序列表：普通命令为 str；`#wa N` → ("delay", ms)；`#N cmd`
    展开为 N 个 str。供 CommandThrottle.enqueue_items 消费。
    """
    items: list = []
    for p in pieces:
        t = (p or "").strip()
        if not t:
            continue
        m = _WA_RE.match(t)
        if m:
            ms = int(m.group(1))
            if 0 < ms <= _MAX_DELAY_MS:
                items.append(("delay", ms))
            continue
        m = _REPEAT_RE.match(t)
        if m:
            n = int(m.group(1))
            cmd = m.group(2).strip()
            if 0 < n <= _MAX_REPEAT and cmd:
                items.extend(cmd for _ in range(n))
            continue
        items.append(t)
    return items