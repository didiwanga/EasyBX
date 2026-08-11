from __future__ import annotations

import re

_GROUP_RE = re.compile(r"^├\s*─{2,}\s*([^─┼┴│\s].*?)\s*─{2,}[┼┤]")
_KEY_RE = re.compile(r"\(([a-zA-Z0-9\-]+)\)")
_SLOT_RE = re.compile(
    r"共使用\s*了?\s*([\d.]+)\s*个技能槽位?\s*[，,]\s*空余槽位\s*\(?\s*([\d.]+)\)?"
)
_SKILL_ROW_RE = _KEY_RE  # 兼容旧导入：session 曾引用该名


class Skill:
    __slots__ = ("key", "name", "category", "level", "desc", "enabled")

    def __init__(self, key: str, name: str, category: str, level: str, desc: str, enabled: bool):
        self.key = key
        self.name = name
        self.category = category
        self.level = level
        self.desc = desc
        self.enabled = enabled

    @property
    def level_num(self) -> float | None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", self.level or "")
        if m:
            return float(m.group(1))
        m = re.search(r"(\d+(?:\.\d+)?)", self.level or "")
        return float(m.group(1)) if m else None

    @property
    def cap_num(self) -> float | None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", self.level or "")
        return float(m.group(2)) if m else None


class SkillsParser:
    """北侠 `skills` 表格：按 `│` 分列解析，兼容 `40.01/-` 无上限与 `□/△` 前缀。"""

    def __init__(self) -> None:
        self.groups: list[str] = []
        self.skills: list[Skill] = []
        self.slots_total = 0.0
        self.slots_used = 0.0

    def parse(self, text: str) -> bool:
        self.groups.clear()
        self.skills.clear()
        category = "通用"
        found_row = False
        for raw in text.replace("\r", "").split("\n"):
            line = raw.strip()
            if not line:
                continue
            gm = _GROUP_RE.match(line)
            if gm:
                category = gm.group(1).strip()
                if category not in self.groups:
                    self.groups.append(category)
                continue
            sm = _SLOT_RE.search(line)
            if sm:
                try:
                    self.slots_used = float(sm.group(1))
                    self.slots_total = float(sm.group(2))
                except ValueError:
                    pass
                continue
            cells = [c.strip() for c in line.split("│") if c.strip()]
            if len(cells) < 3:
                continue
            header_cell, desc, level = cells[0], cells[1], cells[-1]
            km = _KEY_RE.search(header_cell)
            if not km:
                continue  # 表头行「技能/描述/级别上限」无括号拼音，跳过
            key = km.group(1).strip()
            name = header_cell[:km.start()].replace("□", "").strip("　 ")
            if not name or not level:
                continue
            found_row = True
            self.skills.append(Skill(
                key=key, name=name, category=category,
                level=level, desc=desc, enabled=header_cell.startswith("□"),
            ))
        return found_row or bool(self.groups)