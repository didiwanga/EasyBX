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
_EXIT_LINE_RE = re.compile(r"这里明显的(?:方向|出口)[有是][：:]?\s*(.*)")
_NUM_EXIT_RE = re.compile(r"^\d{1,2}$")
# 实体拼音：大写开头、允许中间空格（Shi tan / Wu shu / Mu zhuangzi）
# name 后必须紧跟括号且 name 不以「的」结尾，避免把描述内嵌注解当实体
_ENTITY_RE = re.compile(r"([\u4e00-\u9fff·]{2,5})\s*\(([A-Za-z][A-Za-z ]*)\)")
_DESC_STARTS = ("这里是", "这是一个", "这是(")
# 房间标题：`房间名 - ★` 或 `房间名 - `（地名前置 ASCII 地图，行前缀为 `名称 -`）
_ROOM_TITLE_RE = re.compile(r"^([^-]+?)\s*-\s*★\s*$|^([^-]+?)\s*-\s*$")
# ASCII 地图专用字符（连线/边框/方向符），这些行不可能是房间名/描述
_MAP_CHARS = ("│", "｜", "┌", "┐", "├", "┤", "└", "┘", "＼", "／", "〓", "═", "─", "━")


class LookParser:
    """E7 look 解析层：房间结构/实体/状态技能。

    兼容解析：房间名=`名称 - ★` / `名称 -` 行（前置 ASCII 地图丢弃）；
    `这里明显的(方向|出口)有：` → 出口；`这里是…` 描述；`【】` 状态行；
    `名词(pinyin)` → 实体。任一识别失败都有兜底，不影响整体。
    """

    def __init__(self, bus=None) -> None:
        self.bus = bus

    def parse(self, text: str) -> LookResult | None:
        lines = [l.strip() for l in text.replace("\r", "").split("\n") if l.strip()]
        if not lines:
            return None
        result = LookResult(raw=list(lines))
        room = RoomStructure()
        desc: list[str] = []
        for line in lines:
            em = _EXIT_LINE_RE.search(line)
            if em:
                part = em.group(1) or ""
                exits = [w.strip().lower().strip("。．")
                         for w in re.split(r"[,，、;；\s和]+", part) if w.strip()]
                room.exits = [e for e in exits if e in _EXIT_KEYS or _NUM_EXIT_RE.match(e)]
                continue
            tm = _ROOM_TITLE_RE.match(line)
            if tm:
                room.name = (tm.group(1) or tm.group(2) or "").strip()
                continue
            if any(ch in line for ch in _MAP_CHARS) or (line.count("-") >= 2):
                continue  # ASCII 地图/分隔线
            if line.startswith(_DESC_STARTS):
                desc.append(line)
                continue
            if line.startswith("【") and "】" in line:
                result.status.append(line)
                continue
            for m in re.finditer(_ENTITY_RE, line):
                nm = m.group(1)
                # 跳过描述性注解：名称以语助词/量词开头（「的草图(map)」这类正文内注解）
                if not nm[0].isupper() and any(nm.startswith(p) for p in ("的", "了", "在", "着", "有", "是", "这", "那", "一")):
                    continue
                result.entities.append(Entity(name=nm, english=m.group(2), desc=line))
        if not room.name:
            # 兜底：未识别 `名称 -` 行时退回首行（部分简化输出仅剩描述）
            room.name = lines[0]
        room.desc = desc
        result.room = room
        return result

    def handle_line(self, line: str, account: str | None = None) -> LookResult | None:
        res = self.parse(line)
        if res:
            res.publish(self.bus, account=account)
        return res