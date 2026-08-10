from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class RoomStructure:
    name: str = ""
    desc: list[str] = field(default_factory=list)
    exits: list[str] = field(default_factory=list)
    category: str = ""


@dataclass
class Entity:
    name: str = ""
    english: str = ""
    head: str = ""
    desc: str = ""


@dataclass
class LookResult:
    room: RoomStructure | None = None
    entities: list[Entity] = field(default_factory=list)
    status: list[str] = field(default_factory=list)
    raw: list[str] = field(default_factory=list)

    def publish(self, bus, account: str | None = None) -> None:
        bus.publish("look.parsed", account=account, result=self)


_EXIT_KEYS = {
    "north", "south", "east", "west", "northwest", "northeast",
    "southwest", "southeast", "up", "down", "northup", "southup",
    "eastup", "westup", "northdown", "southdown", "eastdown", "westdown",
    "northeastup", "southeastup", "northwestup", "southwestup",
    "northeastdown", "southeastdown", "northwestdown", "southwestdown",
    "enter", "out",
}
_EXIT_LINE_RE = re.compile(r"这里明显的出口是[：:]\s*(.*)")
_ENTITY_RE = re.compile(r"([\u4e00-\u9fff·]{2,5})\s*\(([a-z]+)\)")
_DESC_STARTS = ("这里是", "这是一个", "这是(")


class LookParser:
    """E7 look 解析层：房间结构/实体/状态技能。

    相容性解析：第一非空行=房间名；`这里明显的出口是：`→出口；
    `这里是…` 描述；`【】` 状态行；`名词(pinyin)`→实体。
    """

    def __init__(self, bus=None) -> None:
        self.bus = bus

    def parse(self, text: str) -> LookResult | None:
        lines = [l.strip() for l in text.replace("\r", "").split("\n") if l.strip()]
        if not lines:
            return None
        result = LookResult(raw=list(lines))
        room = RoomStructure(name=lines[0])
        desc: list[str] = []
        for line in lines:
            em = _EXIT_LINE_RE.search(line)
            if em:
                part = em.group(1) or ""
                exits = [w.strip().lower() for w in re.split(r"[,，;；\s]+", part) if w.strip()]
                room.exits = [e for e in exits if e in _EXIT_KEYS]
                continue
            if line.startswith(_DESC_STARTS):
                desc.append(line)
                continue
            if line.startswith("【") and "】" in line:
                result.status.append(line)
                continue
            for m in re.finditer(_ENTITY_RE, line):
                result.entities.append(Entity(name=m.group(1), english=m.group(2), desc=line))
        room.desc = desc
        result.room = room
        return result

    def handle_line(self, line: str, account: str | None = None) -> LookResult | None:
        res = self.parse(line)
        if res:
            res.publish(self.bus, account=account)
        return res