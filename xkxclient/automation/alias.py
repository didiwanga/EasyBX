from __future__ import annotations

import re


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
        # 别名匹配大小写不敏感：纯小写/纯大写/混合大小写输入均可命中
        try:
            return re.compile(self.pattern, re.IGNORECASE)
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
            # 必须匹配完整输入行：避免 aj 误吞 aj1、pr 误吞 print 等前缀误命中
            if not m or m.end() != len(text):
                continue
            if a.actions:
                from xkxclient.automation.runner import ActionRunner
                ActionRunner(self.session.app.bus, self.session).run(a.actions)
            if not a.replacement:
                return None
            out = a.replacement
            out = out.replace("%0", text)   # %0 = 整行输入
            # 按 %N 从大到小替换，避免 %1 抢先吃掉 %10 的前缀
            for i, g in reversed(list(enumerate(m.groups(), start=1))):
                out = out.replace(f"%{i}", g or "")
            return out
        return None