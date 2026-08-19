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

_DIR_DELTA = {
    "north": (0, 1, 0), "south": (0, -1, 0),
    "east": (1, 0, 0), "west": (-1, 0, 0),
    "northeast": (1, 1, 0), "southeast": (1, -1, 0),
    "northwest": (-1, 1, 0), "southwest": (-1, -1, 0),
    "up": (0, 0, 1), "down": (0, 0, -1),
    "enter": (0, 0, 0), "out": (0, 0, 0),
}


def _norm_dir(d) -> str:
    d = (d or "").strip().lower()
    return d if d in _DIRS else ""


# 移动艰难特征行（河边/江边「一脚深一脚浅」类提示）：发出方向后收到该文本
# 表示移动仍在推进、尚未到达新房间，需要重发同方向直到 GMCP.Move true。
# 可配置 map.tough_patterns 扩展其他变体。
TOUGH_PATTERNS = ("一脚深一脚浅",)


class MapCache(QObject):
    """地图最小集（地图系统.md）：采集 GMCP.Move 房间/出口到本地缓存。

    实例模型：nodes 以实例 id（n0/n1/...）为主键，同名单可多实例（坐标/出口不同）。
    身份识别（与探针 map_probe.py 一致）：出口集一致且坐标一致（或未知）→ 同一实例；
    坐标不符 → 新实例，不合并（同名消歧靠实例化 + 后续区域锚点）。
    坐标：从起点按方向增量累加（north+y/south-y/east+x/west-x、对角线 ±1、up/down ±z）。
    撞墙：result=false 时失败方向记入当前实例 walked，防死循环重试。

    兼容投影：rooms/edges/current（名字键视图）由实例数据全量重建，
    供 mapcanvas/mapdock/LookCapture/Navigator/session 使用，接口签名不变。
    """

    def __init__(self, bus, account: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.bus = bus
        self.account = account
        # 实例数据
        self.nodes: dict[str, dict] = {}          # nid -> {name, coords, exits, walked, neighbors}
        self.name_to_ids: dict[str, list[str]] = {}
        self.current_id: str = ""
        self._nseq = 0
        # 兼容投影（名字键视图）
        self.rooms: dict[str, dict] = {}
        self.edges: dict[str, dict[str, str]] = {}
        self.current: str = ""
        self.dirty_exits: dict[str, list[str]] = {}   # 悬空出口（look 见但未走）
        self._pending_dir: str = ""                   # 用户刚发方向命令（MapSync）
        self._dirty = False
        # 增量同步追踪（mapsync.py 消费）
        self.changed_ids: set[str] = set()            # 自上次上报以来新增/变更的实例
        self.changed_rooms: set[str] = set()          # 自上次上报以来变更的房间元数据
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
        if "nodes" in data and isinstance(data["nodes"], dict):
            for nid, nd in data["nodes"].items():
                nd = dict(nd)
                nd["exits"] = set(nd.get("exits") or [])
                nd["walked"] = set(nd.get("walked") or [])
                nd["neighbors"] = dict(nd.get("neighbors") or {})
                nd["coords"] = nd.get("coords")
                self.nodes[nid] = nd
                self.name_to_ids.setdefault(nd["name"], []).append(nid)
            self.current_id = data.get("current_id") or ""
            self.rooms = data.get("rooms") or {}
            max_n = max((int(n[1:]) for n in self.nodes
                         if n.startswith("n") and n[1:].isdigit()), default=-1)
            self._nseq = max_n + 1
            self._prune_node_neighbors()
        else:
            # 旧格式：rooms/edges 名字键 -> 每名字一个实例
            rooms = data.get("rooms", {}) or {}
            edges = data.get("edges", {}) or {}
            for name, r in rooms.items():
                self._new_node(name, r.get("exits") or [], r.get("coords"))
            for a, eds in edges.items():
                ia = (self.name_to_ids.get(a) or [None])[0]
                for d, b in eds.items():
                    ib = (self.name_to_ids.get(b) or [None])[0]
                    if ia and ib:
                        self._link_ids(ia, d, ib)
            cur = data.get("current") or ""
            if cur:
                ids = self.name_to_ids.get(cur, [])
                self.current_id = ids[0] if ids else self._new_node(cur, [], None)
        self._rebuild_views()
        # 名字键导航骨架：以实例重建为权威（含 look 出口校正），文件中的旧 edges
        # 可能含历史脏边（同房间多方向/enter 等），不再直接采用
        self._prune_edges()

    def flush(self) -> None:
        if not self._dirty:
            return
        nodes_out = {}
        for nid, nd in self.nodes.items():
            nodes_out[nid] = {
                "name": nd["name"],
                "exits": sorted(nd["exits"]),
                "coords": nd["coords"],
                "neighbors": dict(nd["neighbors"]),
            }
        json_write(self._cache_path(), {
            "nodes": nodes_out,
            "name_to_ids": {k: v for k, v in self.name_to_ids.items()},
            "current_id": self.current_id,
            "rooms": self.rooms,
            "edges": self.edges,
            "current": self.current,
        })
        self._dirty = False
        self.bus.publish("map.cache_refreshed", account=self.account,
                         rooms=len(self.rooms))

    # ---- 实例基础 ----
    def _new_node(self, name: str, dirs, coords) -> str:
        nid = "n%d" % self._nseq
        self._nseq += 1
        self.nodes[nid] = {
            "name": name,
            "exits": set(dirs),
            "walked": set(),
            "neighbors": {},
            "coords": list(coords) if coords else None,
        }
        self.name_to_ids.setdefault(name, []).append(nid)
        self.changed_ids.add(nid)
        self.changed_rooms.add(name)
        return nid

    def _resolve_node(self, name: str, dirs: set, expected) -> str | None:
        """身份识别：出口集一致且坐标一致（或未知）→ 复用实例；否则 None（新建）。"""
        for nid in self.name_to_ids.get(name, []):
            nd = self.nodes[nid]
            if set(nd["exits"]) != dirs:
                continue
            c = nd["coords"]
            if expected is None or c is None or tuple(c) == tuple(expected):
                return nid
        return None

    def _link_ids(self, a: str, a_dir: str, b: str) -> None:
        if not a or not b or not a_dir or a not in self.nodes or b not in self.nodes:
            return
        na = self.nodes[a]["neighbors"]
        nb = self.nodes[b]["neighbors"]
        if a_dir in na and na[a_dir] != b:
            return  # 同名歧义：保留已有边
        # 实际行走为准：清理 a 指向 b 的其他方向脏边（同房间经多个方向到同一实例为异常数据）
        for x in [k for k, v in list(na.items()) if v == b and k != a_dir]:
            del na[x]
            op = _DIR_OPPOSITE.get(x)
            if op and nb.get(op) == a:
                del nb[op]
        na[a_dir] = b
        self.changed_ids.add(a)
        self.changed_ids.add(b)
        op = _DIR_OPPOSITE.get(a_dir)
        if op:
            if op in nb and nb[op] != a:
                return
            nb[op] = a

    def _set_current(self, nid: str) -> None:
        self.current_id = nid
        self.current = self.nodes.get(nid, {}).get("name", "")
        self.dirty_exits.pop(self.current, None)

    def _rebuild_views(self) -> None:
        """由实例数据重建名字键投影 rooms/edges/current。"""
        rooms: dict[str, dict] = {}
        edges: dict[str, dict[str, str]] = {}
        for nid, nd in self.nodes.items():
            name = nd["name"]
            r = rooms.setdefault(name, {"exits": []})
            r["exits"] = list(dict.fromkeys([*r.get("exits", []), *sorted(nd["exits"])]))
            if "coords" not in r and nd["coords"]:
                r["coords"] = list(nd["coords"])
            e = edges.setdefault(name, {})
            for d, tid in nd["neighbors"].items():
                if d not in e:
                    e[d] = self.nodes[tid]["name"]
        # 保留既有元数据（category/npc/desc）
        for name, old in self.rooms.items():
            for k in ("category", "npc", "desc"):
                if old.get(k):
                    rooms.setdefault(name, {}).setdefault(k, old[k])
        self.rooms = rooms
        self.edges = edges
        if self.current_id in self.nodes:
            self.current = self.nodes[self.current_id]["name"]

    def _prune_node_neighbors(self) -> None:
        """清理实例邻居脏边：同一实例被多个方向指向时，保留出口声明方向。

        历史探针/采集可能给同一实例写入大量指向同一目标的方向边（如百年堂
        多条 enter/west 指向御道）。仅当同目标存在多条方向时按 look 出口声明
        裁剪；单方向边（含父房间单向进入的子房间）始终保留，避免误删。
        """
        for nd in self.nodes.values():
            nb = nd.get("neighbors") or {}
            if len(nb) <= 1:
                continue
            exits = nd.get("exits") or set()
            by_target: dict[str, str] = {}
            for d, tid in list(nb.items()):
                if tid in by_target:
                    # 同目标多条方向：保留出口声明方向，其次保留第一条
                    if by_target[tid] in exits or d not in exits:
                        del nb[d]
                        continue
                by_target[tid] = d

    def _prune_edges(self) -> None:
        """按 look 出口声明校正 edges：同房间多方向边保留声明方向，丢弃脏方向。

        look 解析的 rooms.exits 为真实出口；edges 中方向不在该房间出口内、
        且出口集里有更合理方向时，用出口方向替换/去重，避免导航第一跳出现
        enter/out 等脏方向。
        """
        for a, eds in list(self.edges.items()):
            exits = set(self.rooms.get(a, {}).get("exits") or [])
            by_target: dict[str, str] = {}
            for d, b in eds.items():
                if b == a:
                    continue
                if b in by_target:
                    # 同目标多条方向：保留出口声明方向，其次保留第一条
                    if by_target[b] in exits or d not in exits:
                        continue
                by_target[b] = d
            self.edges[a] = {d: b for b, d in by_target.items()}

    def _resolve_current(self, name: str) -> str | None:
        if self.current_id and self.nodes.get(self.current_id, {}).get("name") == name:
            return self.current_id
        ids = self.name_to_ids.get(name, [])
        return ids[0] if ids else None

    # ---- 采集 ----
    def set_room(self, name: str, exits: list[str] | None = None,
                 category: str = "", npc: list | None = None,
                 desc: list[str] | None = None) -> None:
        """LookCapture：look 解析回写房间信息（出口并入当前/同名实例）。

        npc 元素可为 str（旧格式中文名）或 (中文名, 英文id) 元组或 {"name","id"}
        dict；含英文 id 时存储为 {"name","id"}，供 ferry/gu 等按 id ask。
        """
        if not name:
            return
        nid = self._resolve_current(name)
        if nid is None:
            nid = self._new_node(name, [], None)
        nd = self.nodes[nid]
        if exits:
            nd["exits"] |= {_norm_dir(e) for e in exits if _norm_dir(e)}
            self.changed_ids.add(nid)
            self.changed_rooms.add(name)
        if category:
            self.rooms.setdefault(name, {})["category"] = category
            self.changed_rooms.add(name)
        if npc:
            r = self.rooms.setdefault(name, {})
            lst = r.setdefault("npc", [])
            for v in npc:
                if isinstance(v, dict):
                    item = {"name": v.get("name", ""), "id": v.get("id", "")}
                elif isinstance(v, (tuple, list)) and len(v) >= 2 and v[1]:
                    item = {"name": v[0], "id": v[1]}
                else:
                    item = {"name": str(v), "id": ""}
                if item and item not in lst:
                    lst.append(item)
                    self.changed_rooms.add(name)
        if desc:
            r = self.rooms.setdefault(name, {})
            cur = r.setdefault("desc", [])
            for v in desc:
                if v and v not in cur:
                    cur.append(v)
                    self.changed_rooms.add(name)
        self._rebuild_views()
        self._dirty = True

    def on_move(self, payload: dict) -> None:
        """GMCP.Move 数据。result=true 时从 short+dir 生成实例+边。"""
        d = payload
        if not isinstance(d, dict):
            d = {}
        ok = d.get("result")
        if not isinstance(ok, bool):
            ok = str(ok or "").strip().lower() in ("true", "1")
        name = str(d.get("short") or "")
        dirs = [_norm_dir(x) for x in (list(d.get("dir") or []))]
        dirs = [x for x in dirs if x]
        if ok and name:
            self._record(name, dirs)
        elif self._pending_dir and self.current_id in self.nodes:
            # result=false 撞墙：失败方向记入 walked，防死循环重试
            self.nodes[self.current_id]["walked"].add(self._pending_dir)
            self._dirty = True
        self._pending_dir = ""
        self.bus.publish("map.pushed", account=self.account)

    def set_pending_dir(self, cmd: str) -> None:
        """MapSync：记录用户发出的方向命令（短名规范化）。"""
        if not self._pending_dir:
            self._pending_dir = _norm_dir(cmd)

    def _record(self, name: str, dirs: list[str]) -> None:
        if not name:
            return
        d = self._pending_dir or self._infer_dir(name)
        prev = self.current_id
        expected = None
        if prev and prev in self.nodes and self.nodes[prev]["coords"]:
            pc = self.nodes[prev]["coords"]
            dd = _DIR_DELTA.get(d, (0, 0, 0))
            expected = (pc[0] + dd[0], pc[1] + dd[1], pc[2] + dd[2])
        nid = self._resolve_node(name, set(dirs), expected)
        if nid is None:
            nid = self._new_node(name, dirs, expected or [0, 0, 0])
        if prev and prev in self.nodes and nid != prev:
            self._link_ids(prev, d, nid)
        self._set_current(nid)
        self.changed_ids.add(nid)
        self.changed_rooms.add(name)
        # 名字键视图重建：新行走房间进入 rooms/edges，地图与导航即时可用
        self._rebuild_views()
        self._prune_node_neighbors()
        self._dirty = True

    def _infer_dir(self, name: str) -> str:
        """无 pending 方向时，依据反向边推断 current -> name 的方向。"""
        if self.current_id and self.current_id in self.nodes:
            for d, nid in self.nodes[self.current_id]["neighbors"].items():
                if self.nodes.get(nid, {}).get("name") == name:
                    return d
        if self.current:
            for d, nxt in self.edges.get(self.current, {}).items():
                if nxt == name:
                    return d
        return ""

    def mark_dirty(self, room: str, dirs: list[str]) -> None:
        """LookCapture：look 解析出但未走的悬空出口。"""
        if room:
            dirty = self.dirty_exits.setdefault(room, [])
            for d in dirs:
                d = _norm_dir(d)
                if d and d not in dirty:
                    dirty.append(d)

    def set_current_name(self, name: str) -> None:
        """look 到的房间作为当前位置（服务器不推 GMCP.Move 时也前进）。"""
        if not name:
            return
        nid = self._resolve_current(name)
        if nid is None:
            nid = self._new_node(name, [], None)
        self._set_current(nid)
        self._rebuild_views()
        self._dirty = True

    def room_coords(self, name: str) -> list | None:
        """名字键房间坐标：优先取实例坐标中离原点最远者（探针真实区域坐标），
        其次名字键元数据；全零占位坐标忽略。
        """
        best = None
        best_d = -1
        for nid in self.name_to_ids.get(name, []):
            c = self.nodes.get(nid, {}).get("coords")
            if c and len(c) >= 2 and not (c[0] == 0 and c[1] == 0):
                d = abs(c[0]) + abs(c[1])
                if d > best_d:
                    best, best_d = c, d
        if best is not None:
            return best
        r = self.rooms.get(name)
        if r and r.get("coords"):
            c = r["coords"]
            if len(c) >= 2 and not (c[0] == 0 and c[1] == 0):
                return c
        return None

    # ---- 寻路 ----
    def _name_adj(self) -> dict[str, dict[str, str]]:
        """名字键双向邻接（含方向）：{name: {neighbor: 方向}}。

        同一对房间存在多条方向边（历史探针/采集脏数据）时，优先保留
        房间 look 出口中声明的方向（rooms.exits），保证导航方向真实可靠。
        """
        adj: dict[str, dict[str, str]] = {}
        for a, eds in self.edges.items():
            exits = set(self.rooms.get(a, {}).get("exits") or [])
            for d, b in eds.items():
                if b == a:
                    continue
                cur = adj.setdefault(a, {})
                if b in cur:
                    # 已有方向：保留声明出口中的方向；都未声明时保留第一条
                    if cur[b] in exits or d not in exits:
                        continue
                cur[b] = d
                op = _DIR_OPPOSITE.get(d)
                if op:
                    oexits = set(self.rooms.get(b, {}).get("exits") or [])
                    rcur = adj.setdefault(b, {})
                    if a in rcur:
                        if rcur[a] in oexits or op not in oexits:
                            continue
                    rcur[a] = op
        return adj

    def route(self, target: str) -> list[str] | None:
        """名字键骨架 BFS（连通性可靠）。返回方向序列（不含起点）。"""
        start = self.current or ""
        if not start:
            return None
        if start == target:
            return []
        adj = self._name_adj()
        if start not in adj:
            return None
        q: deque[str] = deque([start])
        prev: dict[str, tuple[str, str]] = {}
        seen = {start}
        while q:
            cur = q.popleft()
            for nb, d in adj[cur].items():
                if nb in seen:
                    continue
                seen.add(nb)
                prev[nb] = (cur, d)
                if nb == target:
                    path = []
                    node = nb
                    while node != start:
                        p, dd = prev[node]
                        path.append(dd)
                        node = p
                    path.reverse()
                    return path
                q.append(nb)
        return None

    def find_targets(self, name: str, start: str | None = None) -> list[tuple[str, float, list[str]]]:
        """同名实例候选，按当前坐标距离由近到远：[(nid, 距离, 导航路径)]。

        距离用坐标（已按区域/全局坐标系对齐）计算；无坐标实例排后。路径为
        名字键骨架路线（同一名字下各实例共用）。
        """
        ids = self.name_to_ids.get(name, [])
        if not ids:
            return []
        cur = start or self.current_id
        cur_coords = self.nodes[cur].get("coords") if cur and cur in self.nodes else None
        if cur_coords is None and self.current and self.name_to_ids.get(self.current):
            cur_coords = self.nodes[self.name_to_ids[self.current][0]].get("coords")
        cands = []
        for nid in ids:
            c = self.nodes[nid].get("coords")
            if cur_coords and c:
                dist = sum(abs(a - b) for a, b in zip(cur_coords, c))
            else:
                dist = float("inf")
            cands.append((nid, dist))
        cands.sort(key=lambda x: x[1])
        path = self.route(name) or []
        if cur_coords is None:
            # 当前位置无坐标：统一用路径步数作距离，保证候选可比较有数值
            fb = float(len(path))
            return [(nid, fb, list(path)) for nid, _ in cands]
        return [(nid, dist, list(path)) for nid, dist in cands]

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
        desc = list(getattr(room, "desc", None) or [])
        self.cache.set_room(name, exits=list(exits), desc=desc)
        self.cache.mark_dirty(name, exits)
        # look 到的房间作为当前位置：服务器不推 GMCP.Move 时也能让位置/导航前进
        self.cache.set_current_name(name)
        for ent in getattr(result, "entities", None) or []:
            ename = getattr(ent, "name", "")
            if ename:
                self.cache.set_room(name, npc=[(ename, getattr(ent, "english", "") or "")])


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
        self._tough = 0
        self._tough_max = int(ConfigManager.instance().get("map.tough_max", 10))

    def config_step_ms(self, ms: int) -> None:
        self._step_ms = max(800, min(3000, int(ms)))

    def start(self, dirs: list[str]) -> None:
        if not dirs:
            return
        self.reason = ""
        self.queue = list(dirs)
        self.running = True
        self._tough = 0
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
        self._tough = 0
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

    def on_tough_terrain(self) -> None:
        """河边/江边移动艰难（服务器「一脚深一脚浅」类提示）：移动未完成，
        重发当前方向并重置等待计时，GMCP.Move true 才算步进；超上限按卡死停止。
        """
        if not self.running or not self._awaiting or not self.queue:
            return
        self._tough += 1
        if self._tough > self._tough_max:
            self._abort("移动艰难超时")
            return
        d = self.queue[0]
        self.bus.publish("nav.tough", account=getattr(self.session, "account_id", None),
                         step=d, attempt=self._tough)
        self.session.send_auto(d)
        self._arm_timer()

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
