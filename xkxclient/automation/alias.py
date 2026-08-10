from __future__ import annotations

import re

from xkxclient.automation.runner import substitute, split_commands


class Alias:
    def __init__(self, name: str, pattern: str, replacement: str = "",
                 enabled: bool = True, shared: bool = False, actions: list | None = None,
                 group: str = "") -> None:
        self.name = name
        self.pattern = pattern
        self.replacement = replacement
        self.enabled = enabled
        self.shared = shared
        self.actions = actions or []
        self.group = group

    @property
    def regex(self) -> re.Pattern | None:
        try:
            return re.compile(self.pattern)
        except re.error:
            return None


class AliasEngine:
    """B6 别名：regex 匹配 + `%1` 捕获替换 + 可选动作。"""

    def __init__(self, session) -> None:
        self.session = session
        self.aliases: list[Alias] = []

    def load(self, definitions: list[dict]) -> None:
        self.aliases = []
        for d in definitions:
            dd = dict(d)
            self.aliases.append(Alias(
                name=str(dd.get("name", "")),
                pattern=str(dd.get("pattern", "")),
                replacement=str(dd.get("replacement", "")),
                enabled=bool(dd.get("enabled", True)),
                shared=bool(dd.get("shared", False)),
                actions=list(dd.get("actions") or []),
                group=str(dd.get("group", "")),
            ))

    def expand(self, text: str) -> str | None:
        """命中返回替换后命令串（`;` 分隔由 send 拆分），未命中返回 None。"""
        for a in self.aliases:
            if not a.enabled:
                continue
            rx = a.regex
            if rx is None:
                continue
            m = rx.match(text)
            if not m:
                continue
            if a.actions:
                from xkxclient.automation.runner import ActionRunner
                ActionRunner(self.session.app.bus, self.session).run(a.actions)
            if not a.replacement:
                return None
            out = a.replacement
            out = out.replace("%0", text)   # %0 = 整行输入
            for i, g in enumerate(m.groups(), start=1):
                out = out.replace(f"%{i}", g or "")
            return out
        return None