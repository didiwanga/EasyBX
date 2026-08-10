from __future__ import annotations

import re

_GROUP_RE = re.compile(r"─{2,}\s*[:：]?\s*(.{2,20}?)\s*(?:─{2,}|:―{2,}|::)")
_SKILL_ROW_RE = re.compile(
    r"[│丨|]\s*([^\s│|].*?)\s*\(([a-zA-Z0-9\-]+)\)\s*[-─｜|]?\s*(.*)$"
)
_SLOT_RE = re.compile(r"共使用\s*([\d.]+)\s*个技能槽\s*，\s*空余\s+[槽位]+\s*\(?\s*([\d.]+)\)?")


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
    """E-skill_panel：`skills` 命令输出解析。"""

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
            raw = raw.strip()
            if not raw:
                continue
            gm = _GROUP_RE.search(raw)
            if gm:
                category = gm.group(1).strip()
                if category not in self.groups:
                    self.groups.append(category)
                continue
            sm = _SLOT_RE.search(raw)
            if sm:
                try:
                    self.slots_used = float(sm.group(1))
                    self.slots_total = float(sm.group(2))
                except ValueError:
                    pass
                continue
            rm = _SKILL_ROW_RE.search(raw)
            if rm:
                name, key = rm.group(1).strip(), rm.group(2).strip()
                if name and key:
                    enabled = raw.lstrip().startswith("□")
                    skill = Skill(key=key, name=name, category=category,
                                  level=rm.group(3).strip(), desc=rm.group(3).strip(),
                                  enabled=enabled)
                    self.skills.append(skill)
                    found_row = True
        return found_row or bool(self.groups)