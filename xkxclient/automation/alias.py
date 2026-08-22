from __future__ import annotations

import re
import traceback


class Alias:
    def __init__(self, name: str, pattern: str, replacement: str = "",
                 enabled: bool = True, shared: bool = False, actions: list | None = None,
                 group: str = "", handler=None) -> None:
        self.name = name
        self.pattern = pattern
        self.replacement = replacement
        self.enabled = enabled
        self.shared = shared
        self.actions = actions or []
        self.group = group
        # 回调型别名（JS/Lua 脚本动态注册）：命中时调用 handler(text, match)，不发原文。
        # 不持久化——仅存在于运行期，reload 后由脚本宿主重新注册。
        self.handler = handler

    @property
    def regex(self) -> re.Pattern | None:
        # 别名匹配大小写不敏感：纯小写/纯大写/混合大小写输入均可命中
        try:
            return re.compile(self.pattern, re.IGNORECASE)
        except re.error:
            return None


class AliasEngine:
    """B6 别名：regex 匹配 + `%1` 捕获替换 + 可选动作。

    `dynamic` 存放脚本宿主动态注册的回调别名（独立于配置文件列表，
    reload_automation 不清空，unload 时按名移除）。
    """

    def __init__(self, session) -> None:
        self.session = session
        self.aliases: list[Alias] = []
        self.dynamic: list[Alias] = []

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

    # ---- 脚本动态别名 ----
    def register_dynamic(self, alias: Alias) -> None:
        self.remove_dynamic(alias.name)
        self.dynamic.append(alias)

    def remove_dynamic(self, name: str) -> None:
        self.dynamic[:] = [a for a in self.dynamic if a.name != name]

    def find_dynamic(self, name: str) -> Alias | None:
        low = name.lower()
        for a in self.dynamic:
            if a.name.lower() == low:
                return a
        return None

    def expand(self, text: str) -> str | None:
        """命中返回替换后命令串；未命中返回 None；回调别名已消费返回空串。"""
        for a in [*self.aliases, *self.dynamic]:
            if not a.enabled:
                continue
            rx = a.regex
            if rx is None:
                continue
            m = rx.match(text)
            # 必须匹配完整输入行：避免 aj 误吞 aj1、pr 误吞 print 等前缀误命中
            if not m or m.end() != len(text):
                continue
            if a.handler is not None:
                try:
                    a.handler(text, m)
                except Exception:
                    traceback.print_exc()
                return ""
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