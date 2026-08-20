#!/usr/bin/env python3
"""EasyBXb 地图数据同步服务器（纯标准库，无框架依赖）。

采集所有客户端采集的地图数据 → 按实例模型比对合并到 SQLite 主库 →
向客户端增量分发（revision 号），实现上下端地图数据实时同步。

部署：systemd + nginx（端口 5002，nginx 反代 /api/map/ → 127.0.0.1:5002）。

接口：
    POST /api/map/report                     → 客户端上报地图增量，服务端比对合并
    GET  /api/map/snapshot?since=<rev>       → 拉取增量（since=0 全量）
    GET  /api/map/stats                      → 主库统计
    GET  /api/map/route?from=<房间>&to=<房间> → 服务端兜底寻路（跨客户端骨架）

上报/分发数据模型（与客户端 MapCache 实例模型一致）：
    nodes:  {nid: {name, exits[], coords[xyz]|null, neighbors{dir: nid}}}
    rooms:  {name: {exits[], npc[], desc[], category, coords[]}}
    全局 revision：每次主库变更 +1；每客户端记录 last_revision，增量按 revision 过滤。

合并比对规则（与客户端 _resolve_node 一致）：
    身份识别：同名 + 出口集一致 + 坐标一致（或任一未知）→ 同一实例，归并；
              否则新建实例。
    坐标：取非占位（非 [0,0,0]）的绝对值较大者（真实区域坐标）。
    exits：并集。neighbors：双向补边（对端存在时），方向反转为对端出口。
    冲突（同方向指向不同房间/跨客户端坐标矛盾）记入 conflicts 表，不破坏主库。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# 反向方向（与客户端 _DIR_OPPOSITE 一致）
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

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5002
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map_master.db")


def _norm_dir(d) -> str:
    d = (d or "").strip().lower()
    return d if d in _DIRS else ""


def _coord_dist(c) -> int:
    """坐标与原点距离：绝对值较大 = 更可能是真实区域坐标。"""
    if not c or len(c) < 3:
        return 0
    return abs(c[0]) + abs(c[1]) + abs(c[2])


def _coord_t(c):
    if not c or len(c) < 3:
        return None
    return (c[0], c[1], c[2])


class MapStore:
    """SQLite 主库：nodes（实例）+ rooms（元数据）+ conflicts + 客户端 revision。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._load_cache()

    # ---- schema ----
    def _init_schema(self) -> None:
        c = self._conn
        c.executescript("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE IF NOT EXISTS nodes (
            nid TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            exits TEXT NOT NULL DEFAULT '[]',
            coords TEXT,
            neighbors TEXT NOT NULL DEFAULT '{}',
            source TEXT,
            updated_at REAL NOT NULL,
            created_rev INTEGER NOT NULL DEFAULT 0,
            updated_rev INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
        CREATE TABLE IF NOT EXISTS rooms (
            name TEXT PRIMARY KEY,
            npc TEXT, desc TEXT, category TEXT,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            name TEXT, nid TEXT,
            detail TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS clients (
            client_id TEXT PRIMARY KEY,
            account TEXT,
            last_revision INTEGER NOT NULL DEFAULT 0,
            last_seen REAL NOT NULL
        );
        """)
        self._conn.commit()
        if not self._get_meta("revision"):
            self._set_meta("revision", "0")

    # ---- meta ----
    def _get_meta(self, key: str) -> str:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))
        self._conn.commit()

    @property
    def revision(self) -> int:
        return int(self._get_meta("revision") or 0)

    def _bump_rev(self) -> int:
        rev = self.revision + 1
        self._set_meta("revision", str(rev))
        return rev

    # ---- 内存缓存 ----
    def _load_cache(self) -> None:
        self._nodes: dict[str, dict] = {}
        for row in self._conn.execute("SELECT * FROM nodes"):
            self._nodes[row["nid"]] = {
                "name": row["name"],
                "exits": set(json.loads(row["exits"] or "[]")),
                "coords": json.loads(row["coords"]) if row["coords"] else None,
                "neighbors": json.loads(row["neighbors"] or "{}"),
                "source": row["source"],
                "updated_at": row["updated_at"],
                "created_rev": row["created_rev"],
                "updated_rev": row["updated_rev"],
            }
        self._rooms: dict[str, dict] = {}
        for row in self._conn.execute("SELECT * FROM rooms"):
            self._rooms[row["name"]] = {
                "npc": json.loads(row["npc"]) if row["npc"] else [],
                "desc": json.loads(row["desc"]) if row["desc"] else [],
                "category": row["category"] or "",
            }
        self._rebuild_name_index()
        self._nseq = max((int(n[1:]) for n in self._nodes
                          if n.startswith("n") and n[1:].isdigit()), default=-1) + 1

    def _rebuild_name_index(self) -> None:
        """按房间名索引实例，避免合并时对每个上报节点全库扫描（O(N*M)→O(M)）。"""
        idx: dict[str, list[str]] = {}
        for nid, nd in self._nodes.items():
            idx.setdefault(nd["name"], []).append(nid)
        self._name_index = idx

    # ---- 节点存取 ----
    def _new_nid(self) -> str:
        nid = "n%d" % self._nseq
        self._nseq += 1
        return nid

    def _save_node(self, nid: str, nd: dict) -> None:
        # 不在此 commit：merge_report 批量落库后统一提交，避免每节点一次磁盘 fsync
        self._conn.execute(
            "INSERT OR REPLACE INTO nodes(nid, name, exits, coords, neighbors, source,"
            " updated_at, created_rev, updated_rev) VALUES(?,?,?,?,?,?,?,?,?)",
            (nid, nd["name"], json.dumps(sorted(nd["exits"])),
             json.dumps(nd["coords"]) if nd["coords"] is not None else None,
             json.dumps(nd["neighbors"]), nd.get("source"),
             nd.get("updated_at", time.time()),
             nd.get("created_rev", 0), nd.get("updated_rev", 0)))

    # ---- 身份识别 ----
    def _resolve(self, name: str, exits: set, coords):
        """同名 + 出口集一致 + 坐标一致（或任一未知）→ 复用实例；否则 None。"""
        for nid in self._name_index.get(name, []):
            nd = self._nodes[nid]
            if set(nd["exits"]) != exits:
                continue
            c = _coord_t(nd["coords"])
            e = _coord_t(coords)
            if e is None or c is None or c == e:
                return nid
        return None

    def _link(self, a: str, a_dir: str, b: str) -> None:
        """建双向边（对端存在且方向未占用时）。"""
        if not a or not b or not a_dir or a not in self._nodes or b not in self._nodes:
            return
        na = self._nodes[a]["neighbors"]
        nb = self._nodes[b]["neighbors"]
        if a_dir in na and na[a_dir] != b:
            return
        na[a_dir] = b
        op = _DIR_OPPOSITE.get(a_dir)
        if op and op not in nb:
            nb[op] = a

    # ---- 合并上报 ----
    def merge_report(self, report: dict) -> dict:
        """合并客户端上报的地图增量。

        report: {client_id, account, nodes: {nid: {name, exits, coords, neighbors}},
                 rooms: {name: {npc, desc, category}}}
        邻居值可为房间名（跨客户端）或本库 nid；按名字归并后重链。
        返回 {merged_nodes, new_nodes, conflicts, revision, changed}。
        """
        with self._lock:
            client_id = str(report.get("client_id") or "")
            account = str(report.get("account") or "")
            if not client_id:
                client_id = uuid.uuid4().hex[:12]
            now = time.time()
            changed = False
            new_nodes = 0
            conflicts: list[dict] = []
            touched: set[str] = set()
            links: dict[str, dict[str, str]] = {}   # nid -> {dir: 房间名}

            # 1) 归并实例节点
            for _cid, nd in (report.get("nodes") or {}).items():
                if not isinstance(nd, dict):
                    continue
                name = str(nd.get("name") or "")
                if not name:
                    continue
                exits = {_norm_dir(x) for x in (nd.get("exits") or [])}
                exits.discard("")
                coords = nd.get("coords")
                if isinstance(coords, (list, tuple)) and len(coords) >= 3:
                    coords = [coords[0], coords[1], coords[2]]
                else:
                    coords = None
                neigh_names: dict[str, str] = {}
                for d, tn in (nd.get("neighbors") or {}).items():
                    d = _norm_dir(d)
                    if d and tn:
                        neigh_names[d] = str(tn)
                nid = self._resolve(name, exits, coords)
                if nid is None:
                    # 同名+同出口但坐标矛盾：可能是同一房间坐标版本不同。
                    # 记录 conflict；若现有坐标是占位而新坐标更真实，则替换。
                    same_exit = [x for x in self._name_index.get(name, [])
                                 if set(self._nodes[x]["exits"]) == exits]
                    upgrade = None
                    for x in same_exit:
                        oc = _coord_t(self._nodes[x]["coords"])
                        nc = _coord_t(coords)
                        if oc is not None and nc is not None and oc != nc:
                            if _coord_dist(list(oc)) == 0 or _coord_dist(coords) > _coord_dist(list(oc)):
                                upgrade = x
                            conflicts.append({
                                "kind": "coord", "name": name, "nid": x,
                                "detail": {"old": list(oc), "new": coords}})
                    if upgrade is not None:
                        nd_ = self._nodes[upgrade]
                        nd_["coords"] = coords
                        changed = True
                        touched.add(upgrade)
                        continue
                    nid = self._new_nid()
                    self._nodes[nid] = {
                        "name": name, "exits": set(exits), "coords": coords,
                        "neighbors": {}, "source": f"{client_id}:{account}",
                        "updated_at": now, "created_rev": self.revision + 1,
                        "updated_rev": self.revision + 1,
                    }
                    self._name_index.setdefault(name, []).append(nid)
                    new_nodes += 1
                    changed = True
                    touched.add(nid)
                else:
                    nd_ = self._nodes[nid]
                    if coords is not None:
                        oc = _coord_t(nd_["coords"])
                        if oc is None or _coord_dist(list(oc)) < _coord_dist(coords):
                            if oc is not None and oc != _coord_t(coords):
                                conflicts.append({
                                    "kind": "coord", "name": name, "nid": nid,
                                    "detail": {"old": list(oc), "new": coords}})
                            nd_["coords"] = coords
                            changed = True
                            touched.add(nid)
                    new_ex = exits - nd_["exits"]
                    if new_ex:
                        nd_["exits"] |= new_ex
                        changed = True
                        touched.add(nid)
                if neigh_names:
                    # 仅登记待重链的邻居；是否变更由 2) 重链实际新增边决定，
                    # 否则重复上报相同数据也会误判 changed 导致 revision 无限增长
                    links.setdefault(nid, {}).update(neigh_names)

            # 2) 重链邻居：方向 → 主库 nid（同名取首个不同实例）
            for a_nid, ls in links.items():
                for d, tn in ls.items():
                    cands = [x for x in self._name_index.get(tn, []) if x != a_nid]
                    if not cands:
                        continue
                    b = cands[0]
                    existing = self._nodes[a_nid]["neighbors"].get(d)
                    if existing and existing != b:
                        conflicts.append({
                            "kind": "dir", "name": self._nodes[a_nid]["name"],
                            "nid": a_nid,
                            "detail": {"dir": d, "old": self._nodes[existing]["name"],
                                       "new": tn}})
                        continue
                    na = self._nodes[a_nid]["neighbors"]
                    if d not in na:
                        self._link(a_nid, d, b)
                        touched.add(a_nid)
                        touched.add(b)
                        changed = True

            # 3) 合并 rooms 元数据（npc/desc/category）
            rooms = report.get("rooms") or {}
            if isinstance(rooms, dict):
                for rname, r in rooms.items():
                    if not isinstance(r, dict):
                        continue
                    cur = self._rooms.setdefault(str(rname), {})
                    for key in ("npc", "desc"):
                        vals = r.get(key)
                        if isinstance(vals, list) and vals:
                            cur_lst = cur.setdefault(key, [])
                            for v in vals:
                                if isinstance(v, dict):
                                    if v not in cur_lst:
                                        cur_lst.append(v)
                                        changed = True
                                elif v not in cur_lst:
                                    cur_lst.append(v)
                                    changed = True
                    cat = r.get("category")
                    if cat and cur.get("category") != cat:
                        cur["category"] = cat
                        changed = True

            # 4) 批量落库 + revision（一次事务提交，避免每节点一次 fsync）
            if changed:
                rev = self._bump_rev()
                for nid in touched:
                    nd = self._nodes[nid]
                    nd["updated_rev"] = rev
                    self._save_node(nid, nd)
                # 只写本次上报涉及的房间，不整表回写
                rrows = []
                for rname in (report.get("rooms") or {}):
                    r = self._rooms.get(rname)
                    if not r:
                        continue
                    rrows.append((rname, json.dumps(r.get("npc", [])),
                                  json.dumps(r.get("desc", [])),
                                  r.get("category", ""), now))
                if rrows:
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO rooms(name, npc, desc, category, updated_at)"
                        " VALUES(?,?,?,?,?)", rrows)
                crows = [(c["kind"], c.get("name"), c.get("nid"),
                          json.dumps(c.get("detail", {}), ensure_ascii=False), now)
                         for c in conflicts]
                if crows:
                    self._conn.executemany(
                        "INSERT INTO conflicts(kind, name, nid, detail, created_at)"
                        " VALUES(?,?,?,?,?)", crows)
                self._conn.commit()
            else:
                rev = self.revision

            # 5) 客户端 tracking
            self._conn.execute(
                "INSERT OR REPLACE INTO clients(client_id, account, last_revision, last_seen)"
                " VALUES(?,?,?,?)",
                (client_id, account, rev, now))
            self._conn.commit()

            return {
                "merged_nodes": len(report.get("nodes") or {}),
                "new_nodes": new_nodes,
                "conflicts": len(conflicts),
                "revision": rev,
                "changed": changed,
            }

    # ---- 快照（增量分发）----
    def snapshot(self, since: int, client_id: str = "") -> dict:
        """返回自 since 以来变更的节点/元数据（since<=0 全量）。"""
        with self._lock:
            now = time.time()
            nodes_out: dict[str, dict] = {}
            if since <= 0:
                for nid, nd in self._nodes.items():
                    nodes_out[nid] = {
                        "name": nd["name"],
                        "exits": sorted(nd["exits"]),
                        "coords": nd["coords"],
                        "neighbors": dict(nd["neighbors"]),
                    }
            else:
                for nid, nd in self._nodes.items():
                    if nd["created_rev"] > since or nd["updated_rev"] > since:
                        nodes_out[nid] = {
                            "name": nd["name"],
                            "exits": sorted(nd["exits"]),
                            "coords": nd["coords"],
                            "neighbors": dict(nd["neighbors"]),
                        }
            rooms_out: dict[str, dict] = {}
            for rname, r in self._rooms.items():
                rooms_out[rname] = {
                    "exits": self._room_exits(rname),
                    "npc": list(r.get("npc", [])),
                    "desc": list(r.get("desc", [])),
                    "category": r.get("category", ""),
                }
            if client_id:
                self._conn.execute(
                    "INSERT OR REPLACE INTO clients(client_id, account, last_revision,"
                    " last_seen) VALUES(?,?,(SELECT last_revision FROM clients"
                    " WHERE client_id=?),?)",
                    (client_id, "", self.revision, now))
                self._conn.commit()
            return {"revision": self.revision, "nodes": nodes_out, "rooms": rooms_out}

    def _room_exits(self, name: str) -> list[str]:
        out: set[str] = set()
        for nid in self._name_index.get(name, []):
            out |= set(self._nodes[nid]["exits"])
        return sorted(out)

    # ---- 统计 ----
    def stats(self) -> dict:
        with self._lock:
            n_nodes = len(self._nodes)
            n_rooms = len({nd["name"] for nd in self._nodes.values()})
            n_edges = sum(len(nd["neighbors"]) for nd in self._nodes.values()) // 2
            n_clients = self._conn.execute(
                "SELECT COUNT(*) AS c FROM clients").fetchone()["c"]
            n_conflicts = self._conn.execute(
                "SELECT COUNT(*) AS c FROM conflicts").fetchone()["c"]
            return {
                "revision": self.revision,
                "nodes": n_nodes,
                "rooms": n_rooms,
                "edges": n_edges,
                "clients": n_clients,
                "conflicts": n_conflicts,
            }

    # ---- 兜底寻路（名字键 BFS，跨客户端骨架）----
    def route(self, from_name: str, to_name: str) -> list[str] | None:
        with self._lock:
            if from_name == to_name:
                return []
            # 名字 → 邻居名字表
            adj: dict[str, dict[str, str]] = {}
            for nid, nd in self._nodes.items():
                name = nd["name"]
                d = adj.setdefault(name, {})
                for dir_, tid in nd["neighbors"].items():
                    if tid in self._nodes:
                        d[dir_] = self._nodes[tid]["name"]
            from collections import deque
            q = deque([from_name])
            prev = {from_name: None}
            while q:
                cur = q.popleft()
                if cur == to_name:
                    break
                for dir_, nb in adj.get(cur, {}).items():
                    if nb not in prev:
                        prev[nb] = (cur, dir_)
                        q.append(nb)
            if to_name not in prev:
                return None
            path = []
            node = to_name
            while node != from_name:
                step = prev[node]
                path.append(step[1])
                node = step[0]
            return path[::-1]


class Handler(BaseHTTPRequestHandler):
    server_version = "EasyBXbMapSync/1.0"
    store: MapStore = None  # type: ignore

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)
        if path == "/api/map/snapshot":
            try:
                since = int((q.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            client = (q.get("client") or [""])[0]
            data = self.store.snapshot(since, client)
            self.send_json(data)
            return
        if path == "/api/map/stats":
            self.send_json(self.store.stats())
            return
        if path == "/api/map/route":
            frm = (q.get("from") or [""])[0]
            to = (q.get("to") or [""])[0]
            route = self.store.route(frm, to)
            self.send_json({"ok": route is not None, "route": route or []})
            return
        if path == "/api/map/ping":
            self.send_json({"ok": True, "time": time.time()})
            return
        self.send_json({"error": "not found"}, code=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "bad json"}, code=400)
            return
        if path == "/api/map/report":
            if not isinstance(body, dict):
                self.send_json({"error": "bad payload"}, code=400)
                return
            try:
                result = self.store.merge_report(body)
            except Exception as exc:
                self.send_json({"error": f"merge failed: {exc}"}, code=500)
                return
            self.send_json({"ok": True, **result})
            return
        self.send_json({"error": "not found"}, code=404)

    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))


def main():
    store = MapStore(DB_PATH)
    Handler.store = store
    print(f"[map_sync_server] listening on 0.0.0.0:{PORT} (db={DB_PATH})", flush=True)
    # 多线程：大合并不再阻塞 stats/拉取与其他客户端请求
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()