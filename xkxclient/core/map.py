from __future__ import annotations

from collections import deque

from PyQt6.QtCore import QObject, QTimer

from xkxclient.core.config import json_read, json_write, ConfigManager

# 地图系统.md：MapSync / MapCache / LookCapture / Navigator

_DIR_OPPOSITE = {
    "north": "south", "south": "north", "east": "west", "west": "east",
    "northwest": "southeast", "southeast": "northwest",
    "northeast": "southwest", "southwest": "northeast",
    "up": "down", "down": "up",
    "northup": "southdown", "southdown": "northup",
    "eastup": "westdown", "westdown": "eastup",
    "northdown": "southup", "southup": "northdown",
    "eastdown": "westup", "westup": "eastdown",
    "northeastup": "southwestdown", "southwestdown": "northeastup",
    "southeastup": "northwestdown", "northwestdown": "southeastup",
    "northeastdown": "southwestup", "southwestup": "northeastdown",
    "southeastdown": "northwestup", "northwestup": "southeastdown",
    "enter": "out", "out": "enter",
}
_DIRS = set(_DIR_OPPOSITE)


def _norm_dir(d) -> str:
    d = (d or "").strip().lower()
    return d if d in _DIRS else ""


class MapCache(QObject):
    """地图最小集（地图系统.md）：采集 GMCP.Move 房间/出口到本地缓存。

    - rooms: name -> {"exits": [dir..], "category": "", "npc": [], "desc": []}
    - edges: from_room -> {dir: to_room}（方向感知，供 BFS 出方向序列）
    - current: 玩家当前位置房间名
    导航：缓存内 BFS 最短路径（返回方向序列，真正可 walk）。
    """

    def __init__(self, bus, account: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.bus = bus
        self.account = account
        self.rooms: dict[str, dict] = {}
        self.edges: dict[str, dict[str, str]] = {}
        self.current: str = ""
        self.dirty_exits: dict[str, list[str]] = {}   # 悬空出口（look 见但未走）
        self._pending_dir: str = ""                   # 用户刚发方向命令（MapSync）
        self._dirty = False
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(15000)
        self._flush_timer.timeout.connect(self.flush)
        self._flush_timer.start()
        self._load()

    # ---- 持久化 ----
    def _cache_path(self):
        return ConfigManager.instance().root / "config" / "map_cache.json"

    def _load(self) -> None:
        data = json_read(self._cache_path())
        if not isinstance(data, dict):
            return
        self.rooms = data.get("rooms", {}) or {}
        self.edges = data.get("edges", {}) or {}
        self.current = data.get("current", "") or ""

    def flush(self) -> None:
        if not self._dirty:
            return
        json_write(self._cache_path(), {
            "rooms": self.rooms, "edges": self.edges, "current": self.current,
        })
        self._dirty = False
        self.bus.publish("map.cache_refreshed", account=self.account,
                         rooms=len(self.rooms))

    # ---- 采集 ----
    def set_room(self, name: str, exits: list[str] | None = None,
                 category: str = "", npc: list[str] | None = None,
                 desc: list[str] | None = None) -> None:
        """LookCapture：look 解析回写房间信息。"""
        if not name:
            return
        node = self.rooms.setdefault(name, {"exits": []})
        if exits:
            node["exits"] = list(dict.fromkeys([_norm_dir(e) for e in exits]))
        if category:
            node["category"] = category
        if npc:
            node["npc"] = list(dict.fromkeys(npc))
        if desc:
            node.setdefault("desc", []).extend(desc)
        self._dirty = True

    def on_move(self, payload: dict) -> None:
        """GMCP.Move 数据。result=true 时从 short+dir 生成房间+边。"""
        d = payload
        if not isinstance(d, dict):
            d = {}
        ok = str(d.get("result", "")).lower() == "true"
        name = str(d.get("short") or "")
        dirs = [ _norm_dir(x) for x in (list(d.get("dir") or [])) ]
        dirs = [x for x in dirs if x]
        if ok and name:
            self._record(name, dirs)
        elif self._pending_dir and name:
            # result=false 撞墙：只记 pending 已清，不更新房间
            pass
        self._pending_dir = ""
        self.bus.publish("map.pushed", account=self.account)

    def set_pending_dir(self, cmd: str) -> None:
        """MapSync：记录用户发出的方向命令（短名规范化）。"""
        if not self._pending_dir:
            self._pending_dir = _norm_dir(cmd)

    def _record(self, name: str, dirs: list[str]) -> None:
        if not name:
            return
        if self.current and name != self.current:
            prev_dir = self._pending_dir or self._infer_dir(self.current, name)
            self._link(self.current, prev_dir, name)
        self.current = name
        node = self.rooms.setdefault(name, {"exits": []})
        node["exits"] = list(dict.fromkeys([e for e in (dirs + node.get("exits", [])) if e]))
        node.setdefault("category", "")
        self.dirty_exits.pop(name, None)
        self._dirty = True

    def _infer_dir(self, a: str, b: str) -> str:
        """无 pending 方向时，依据反向边推断 a->b 的方向。"""
        for d, nxt in self.edges.get(a, {}).items():
            if nxt == b:
                return d
        return ""

    def _link(self, a: str, a_dir: str, b: str) -> None:
        """记录 房间a -(a_dir)-> 房间b，并补反向边（无向边）。"""
        if not a or not b or not a_dir:
            return
        a_edges = self.edges.setdefault(a, {})
        if b in a_edges.values():
            return
        a_edges[a_dir] = b
        op = _DIR_OPPOSITE.get(a_dir)
        if op:
            self.edges.setdefault(b, {})[op] = a

    def mark_dirty(self, room: str, dirs: list[str]) -> None:
        """LookCapture：look 解析出但未走的悬空出口。"""
        if room:
            dirty = self.dirty_exits.setdefault(room, [])
            for d in dirs:
                d = _norm_dir(d)
                if d and d not in dirty:
                    dirty.append(d)

    # ---- 寻路 ----
    def route(self, target: str) -> list[str] | None:
        """缓存内 BFS 最短路径，返回方向序列（不含起点）。"""
        start = self.current
        if not start or start not in self.edges:
            return None
        if start == target:
            return []
        q: deque[str] = deque([start])
        prev: dict[str, tuple[str, str]] = {}   # room -> (prev_room, dir)
        seen = {start}
        while q:
            cur = q.popleft()
            for d, nxt in self.edges.get(cur, {}).items():
                if nxt in seen:
                    continue
                seen.add(nxt)
                prev[nxt] = (cur, d)
                if nxt == target:
                    path = []
                    node = nxt
                    while node != start:
                        p, d = prev[node]
                        path.append(d)
                        node = p
                    path.reverse()
                    return path
                q.append(nxt)
        return None

    def push_moves(self, moves: list[dict]) -> None:
        """MapSync 批量上报的服务端回写（本地缓存已覆盖时无需操作）。"""
        for m in moves or []:
            if isinstance(m, dict):
                self.on_move(m)


class LookCapture(QObject):
    """LookCapture（E7 + 地图系统.md）：look 解析结果回写地图缓存。

    进入新房间自动 look 由 session.auto_look 驱动（见 B/E7）。
    """

    def __init__(self, bus, cache: MapCache, parent=None) -> None:
        super().__init__(parent)
        self.bus = bus
        self.cache = cache
        self.bus.subscribe("look.parsed", self._on_parsed)

    def _on_parsed(self, payload: dict) -> None:
        result = payload.get("result")
        if result is None:
            return
        room = getattr(result, "room", None)
        if room is None:
            return
        name = getattr(room, "name", "")
        if not name:
            return
        exits = getattr(room, "exits", None) or []
        self.cache.set_room(name, exits=list(exits))
        self.cache.mark_dirty(name, exits)
        for ent in getattr(result, "entities", None) or []:
            ename = getattr(ent, "name", "")
            if ename:
                node = self.cache.rooms.setdefault(name, {"exits": []})
                npc = node.setdefault("npc", [])
                if ename not in npc:
                    npc.append(ename)


class Navigator(QObject):
    """自动 walk（地图系统.md 二 2.3）：逐条发送 + 每步确认 + 卡停 + 事件总线。"""

    def __init__(self, cache: MapCache, session, bus, parent=None) -> None:
        super().__init__(parent)
        self.cache = cache
        self.session = session
        self.bus = bus
        self.queue: list[str] = []
        self.running = False
        self.reason = ""
        self._step_ms = int(ConfigManager.instance().get("map.step_ms", 1500))
        self._timer: QTimer | None = None
        self._awaiting = False
        self._timeouts = 0

    def config_step_ms(self, ms: int) -> None:
        self._step_ms = max(800, min(3000, int(ms)))

    def start(self, dirs: list[str]) -> None:
        if not dirs:
            return
        self.reason = ""
        self.queue = list(dirs)
        self.running = True
        self.bus.publish("nav.start", account=getattr(self.session, "account_id", None),
                         total=len(self.queue), steps=self.queue)
        self._step()

    def _step(self) -> None:
        if not self.running:
            return
        if not self.queue:
            self._finish()
            return
        d = self.queue[0]
        self._awaiting = True
        self.session.send_auto(d)
        self.bus.publish("nav.step", account=getattr(self.session, "account_id", None),
                         step=d, remaining=list(self.queue))
        self._arm_timer()

    def _arm_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(self._on_timeout)
        t.start(max(self._step_ms, 1500))
        self._timer = t

    def on_move(self, ok: bool) -> None:
        """GMCP.Move result。true=确认步进；false=撞墙立即停。"""
        if self._timer is not None:
            self._timer.stop()
        self._timeouts = 0
        if not self.running:
            return
        if not ok:
            self._abort("撞墙")
            return
        if self.queue:
            self.queue.pop(0)
        if not self.queue:
            self._finish()
        else:
            self._step()

    def _on_timeout(self) -> None:
        if not self.running or not self._awaiting:
            return
        self._timeouts += 1
        if self._timeouts >= 3:
            self._abort("连续超时")
        else:
            self._arm_timer()

    def stop(self) -> None:
        self._stop()

    def _stop(self) -> None:
        self.running = False
        self._awaiting = False
        if self._timer is not None:
            self._timer.stop()
        self.bus.publish("nav.stopped", account=getattr(self.session, "account_id", None),
                         reason=self.reason or "手动停止")

    def _abort(self, reason: str) -> None:
        self.reason = reason
        self.bus.publish("nav.stuck", account=getattr(self.session, "account_id", None),
                         reason=reason)
        self._stop()

    def _finish(self) -> None:
        self.running = False
        self._awaiting = False
        if self._timer is not None:
            self._timer.stop()
        self.bus.publish("nav.arrived", account=getattr(self.session, "account_id", None))
