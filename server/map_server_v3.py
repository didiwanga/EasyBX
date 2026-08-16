#!/usr/bin/env python3
"""世界地图 API 服务器 v2 (2026-08-04 重构)。

相对 v1 的变化:
- 房间主键从 room_name 改成 room_id (md5(name)[:6] + ts)
- 旧 edges 表改成 exits 表: (room_id, dir, to_room_id) 主键
- to_room_id 可空 = 悬空出口 (look 看到但没走过)
- 服务端接收单边 [A, dir, B] 自动反推 [B, reverse(dir), A]
- 客户端上报协议改成 /api/map/moves (moves + exits + rooms 三类)
- description / objects 留空,客户端手动 look 时补充
- 旧 tables rooms/edges 改名为 *_legacy, 不再写入

部署: systemd + nginx (同 v1)
"""

import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlparse

# 宏分享端点（server/macro_server.py 部署在同目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from macro_server import handle_get_macros, handle_post_macros
from user_server import handle_get_user, handle_post_user, _check_token

DB_PATH = os.environ.get("MAP_DB") or (sys.argv[2] if len(sys.argv) > 2 else "/var/www/pytools-releases/map_v2.db")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5001
MAX_QUERY = 50


# ── 常量 ──────────────────────────────────────────

DIRS_REVERSE = {
    # 8 方位 cardinal + diagonal
    "e":  "w",   "w":  "e",
    "n":  "s",   "s":  "n",
    "ne": "sw",  "sw": "ne",
    "nw": "se",  "se": "nw",
    # 垂直
    "u":  "d",   "d":  "u",
    "enter": "out", "out": "enter",
    # 8 方位上下 (同角但 vertical style)
    "eu": "wd",  "wd": "eu",
    "ed": "wu",  "wu": "ed",
    "nu": "sd",  "sd": "nu",
    "nd": "su",  "su": "nd",
}

DIR_ANGLE = {
    "n": -90, "ne": -45, "e": 0, "se": 45,
    "s":  90, "sw": 135, "w": 180, "nw": -135,
    "u": -90, "d": 90, "enter": -90, "out": 90,
    "nu": -90, "nd": -90, "su": 90, "sd": 90,
    "eu": 0, "ed": 0, "wu": 180, "wd": 180,
}

DIR_STYLE = {
    **{d: "cardinal" for d in
       ["n","s","e","w","ne","nw","se","sw"]},
    **{d: "vertical" for d in
       ["u","d","enter","out",
        "nu","nd","su","sd","eu","ed","wu","wd"]},
}

VALID_DIRS = set(DIRS_REVERSE.keys()) | set(DIRS_REVERSE.values())
# 长名 (north/south/...) 也合法,反查时 strip
DIR_LONG_ALIAS = {
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
    "up": "u", "down": "d", "enter": "enter", "out": "out",
    "northup": "nu", "southup": "su", "eastup": "eu", "westup": "wu",
    "northdown": "nd", "southdown": "sd", "eastdown": "ed", "westdown": "wd",
}
for _long, _short in DIR_LONG_ALIAS.items():
    VALID_DIRS.add(_long)
    VALID_DIRS.add(_short)


# ── ID 派生 ──────────────────────────────────────────

def make_room_id(name: str) -> str:
    """name → 'md5hex[:6]' + '-' + unix_ts (短整数)

    同名房间每次都生成新 ID (A 方案: 不合并重名)。
    """
    if not name:
        return f"unknown-{int(time.time())}"
    h = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    return f"{h}-{int(time.time())}"


# ── 数据库 ──────────────────────────────────────────

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = db_connect()
    cur = conn.cursor()

    # 旧数据全部丢弃 (用户 2026-08-04 决策: 从零开始, 不平移脏数据)
    # 2026-08-04 14:52: 先关 FK 再 DROP, 避免 "FOREIGN KEY constraint failed" 错误
    # 2026-08-04 18:10: 加 DROP exits / move_log — 否则重启后旧 exit 引用旧 rid (md5+ts 在每次启动都不同),
    # 导致 edges 导出时 id_to_name 找不到映射, 客户端画出 rid 当房间名
    cur.execute("PRAGMA foreign_keys = OFF")
    for old_table in ("rooms", "edges", "exits", "move_log", "rooms_legacy", "edges_legacy"):
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (old_table,),
        )
        if cur.fetchone() is not None:
            cur.execute(f"DROP TABLE {old_table}")
            print(f"[init_db] DROP {old_table}", flush=True)
    cur.execute("PRAGMA foreign_keys = ON")

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS rooms (
            room_id       TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            area          TEXT,
            description   TEXT,
            exits_json    TEXT,
            objects       TEXT,
            pos_x         REAL,
            pos_y         REAL,
            updated_at    REAL,
            updated_by    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_rooms_name ON rooms(name);
        CREATE INDEX IF NOT EXISTS idx_rooms_area ON rooms(area);

        CREATE TABLE IF NOT EXISTS exits (
            room_id        TEXT NOT NULL,
            dir            TEXT NOT NULL,
            to_room_id     TEXT,
            source         TEXT,
            verified_count INTEGER DEFAULT 0,
            updated_at     REAL,
            updated_by     TEXT,
            PRIMARY KEY (room_id, dir),
            FOREIGN KEY (room_id) REFERENCES rooms(room_id),
            FOREIGN KEY (to_room_id) REFERENCES rooms(room_id)
        );
        CREATE INDEX IF NOT EXISTS idx_exits_to ON exits(to_room_id);
        CREATE INDEX IF NOT EXISTS idx_exits_null_to ON exits(to_room_id)
            WHERE to_room_id IS NULL;

        CREATE TABLE IF NOT EXISTS move_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            frm_id        TEXT NOT NULL,
            dir           TEXT NOT NULL,
            to_id         TEXT NOT NULL,
            ts            REAL,
            client_id     TEXT,
            reverse_valid INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_move_frm ON move_log(frm_id);
        CREATE INDEX IF NOT EXISTS idx_move_to ON move_log(to_id);

        CREATE TABLE IF NOT EXISTS areas (
            abbr        TEXT PRIMARY KEY,
            name_en     TEXT,
            name_ch     TEXT,
            position    TEXT,
            updated_at  REAL
        );
        CREATE TABLE IF NOT EXISTS area_edges (
            frm         TEXT,
            "to"        TEXT,
            steps       TEXT,
            PRIMARY KEY (frm, "to")
        );
        CREATE INDEX IF NOT EXISTS idx_area_edges_to ON area_edges("to");
        """
    )
    conn.commit()
    conn.close()


# ── 业务:房间 + exits ──────────────────────────────────────────

def upsert_room(conn: sqlite3.Connection, room: dict, client: str) -> str:
    """插入或更新房间, 返回 room_id (客户端可能没传,服务端分配)。"""
    rid = (room.get("room_id") or "").strip()
    name = (room.get("name") or "").strip()
    if not name and not rid:
        return ""

    now = time.time()
    if not rid:
        # 客户端没传 ID (老协议 / 第一次上报) → 服务端派生
        # 同名复用最新一条 (避免每次首次见就建新 ID)
        row = conn.execute(
            "SELECT room_id, updated_at FROM rooms WHERE name=? ORDER BY updated_at DESC LIMIT 1",
            (name,),
        ).fetchone()
        if row is not None:
            rid = row["room_id"]
        else:
            rid = make_room_id(name)

    row = conn.execute("SELECT updated_at FROM rooms WHERE room_id=?", (rid,)).fetchone()
    ts = float(room.get("updated_at") or now)
    if row is not None and row["updated_at"] is not None and ts < row["updated_at"] - 1.0:
        return rid  # 旧数据,跳过

    # pos 可选: 客户端传了才更新 (服务端不从 BFS 自动推位置, 只存客户端布局后的值)
    pos_x = room.get("pos_x")
    pos_y = room.get("pos_y")
    if pos_x is not None:
        pos_x = float(pos_x)
    if pos_y is not None:
        pos_y = float(pos_y)

    conn.execute(
        """INSERT INTO rooms (room_id, name, area, description, exits_json, objects, pos_x, pos_y, updated_at, updated_by)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(room_id) DO UPDATE SET
             name=excluded.name,
             area=COALESCE(NULLIF(excluded.area, ''), rooms.area),
             description=CASE WHEN excluded.description != '' THEN excluded.description ELSE rooms.description END,
             exits_json=COALESCE(NULLIF(excluded.exits_json, ''), rooms.exits_json),
             objects=CASE WHEN excluded.objects != '[]' THEN excluded.objects ELSE rooms.objects END,
             pos_x=COALESCE(excluded.pos_x, rooms.pos_x),
             pos_y=COALESCE(excluded.pos_y, rooms.pos_y),
             updated_at=excluded.updated_at,
             updated_by=excluded.updated_by
        """,
        (
            rid,
            name or "",
            (room.get("area") or "")[:200],
            (room.get("description") or "")[:4000],
            json.dumps(room.get("exits") or [], ensure_ascii=False),
            json.dumps(room.get("objects") or [], ensure_ascii=False),
            pos_x,
            pos_y,
            ts,
            (client or "")[:64],
        ),
    )
    return rid


def upsert_exit(conn: sqlite3.Connection, room_id: str, d: str,
                to_room_id: str | None, source: str,
                ts: float, client: str) -> str:
    """插入/更新一条 exit。返回 'inserted' / 'updated' / 'unchanged'。
    2026-08-04 14:48 FK 降级: to_room_id 在服务端 rooms 不存在时自动降级为 None (悬空),
    避免客户端发的 room_id 引用不存在的房间时 FK 失败抛 502。
    """
    if not room_id or not d:
        return "skipped"
    # FK 降级: 检查 room_id 自身是否存在
    if not conn.execute("SELECT 1 FROM rooms WHERE room_id=?", (room_id,)).fetchone():
        # room_id 本身都不在 rooms 表, 跳过
        return "skipped"
    # FK 降级: 检查 to_room_id 是否存在, 不存在则降级为悬空
    if to_room_id:
        if not conn.execute("SELECT 1 FROM rooms WHERE room_id=?", (to_room_id,)).fetchone():
            to_room_id = None  # 降级为悬空
    row = conn.execute(
        "SELECT to_room_id, updated_at FROM exits WHERE room_id=? AND dir=?",
        (room_id, d),
    ).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO exits (room_id, dir, to_room_id, source, verified_count, updated_at, updated_by)
               VALUES (?,?,?,?,?,?,?)""",
            (room_id, d, to_room_id, source, 0 if to_room_id else 0, ts, client[:64]),
        )
        return "inserted"
    # 已存在
    if row["to_room_id"] == to_room_id:
        # 一致: 验证一次 (verified_count++) 仅当 to_room_id 非空
        if to_room_id:
            conn.execute(
                """UPDATE exits SET verified_count = verified_count + 1,
                                    updated_at = ?, updated_by = ?
                   WHERE room_id=? AND dir=?""",
                (ts, client[:64], room_id, d),
            )
        return "unchanged"
    # 不一致 (to_room_id 变化)
    if row["to_room_id"] is None and to_room_id is not None:
        # 悬空→已知 (升级)
        conn.execute(
            """UPDATE exits SET to_room_id=?, source='verified',
                                verified_count=verified_count+1,
                                updated_at=?, updated_by=?
               WHERE room_id=? AND dir=?""",
            (to_room_id, ts, client[:64], room_id, d),
        )
        return "updated"
    # 其他冲突 (已指向其他房间): 保留先到者,标迷宫嫌疑
    return "conflict"


def normalize_dir(d: str) -> str:
    """长名 (north) → 短名 (n)。未知返回原值。"""
    return DIR_LONG_ALIAS.get(d, d)


def apply_move(conn: sqlite3.Connection, frm_id: str, d: str, to_id: str,
               ts: float, client: str) -> dict:
    """应用一条移动事件: 写正向 + 反向 + 日志。"""
    if not frm_id or not to_id:
        return {"status": "skipped", "reason": "missing_id"}
    d_short = normalize_dir(d)
    if d_short not in DIRS_REVERSE:
        return {"status": "skipped", "reason": f"unknown_dir:{d}"}

    rev = DIRS_REVERSE[d_short]

    # 1. 写正向
    status_fwd = upsert_exit(conn, frm_id, d_short, to_id, "observed", ts, client)
    # 2. 写反向 (自动反推)
    status_rev = upsert_exit(conn, to_id, rev, frm_id, "derived", ts, client)
    # 3. 日志
    conn.execute(
        "INSERT INTO move_log (frm_id, dir, to_id, ts, client_id) VALUES (?,?,?,?,?)",
        (frm_id, d_short, to_id, ts, client[:64]),
    )
    return {"status": "applied", "fwd": status_fwd, "rev": status_rev,
            "dir_normalized": d_short}


def apply_exits(conn: sqlite3.Connection, room_id: str,
                exits_list: list[dict], client: str) -> dict:
    """批量上报 exits (含悬空)。每个 exit 是 {dir, to_id(可空)}。"""
    added = updated = skipped = 0
    now = time.time()
    for e in exits_list:
        d = normalize_dir((e.get("dir") or "").strip())
        to_id = (e.get("to_id") or "").strip() or None
        if not d or d not in DIRS_REVERSE:
            skipped += 1
            continue
        r = upsert_exit(conn, room_id, d, to_id, "observed", now, client)
        if r == "inserted":
            added += 1
        elif r == "updated":
            updated += 1
        else:
            skipped += 1
    conn.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


# ── 业务:areas / 旧 GET (保留兼容) ──────────────────────────────────────────

def upsert_areas(conn: sqlite3.Connection, areas: list[dict]) -> dict:
    added = updated = 0
    for a in areas:
        abbr = (a.get("abbr") or "").strip()
        if not abbr:
            continue
        row = conn.execute("SELECT abbr FROM areas WHERE abbr=?", (abbr,)).fetchone()
        conn.execute(
            """INSERT OR REPLACE INTO areas (abbr, name_en, name_ch, position, updated_at)
               VALUES (?,?,?,?,?)""",
            (
                abbr,
                (a.get("name_en") or "")[:64],
                (a.get("name_ch") or "")[:64],
                (a.get("position") or "")[:128],
                time.time(),
            ),
        )
        if row is None:
            added += 1
        else:
            updated += 1
    conn.commit()
    return {"added": added, "updated": updated}


def upsert_area_edges(conn: sqlite3.Connection, edges: list[dict]) -> dict:
    added = updated = 0
    for e in edges:
        frm = (e.get("frm") or "").strip()
        to = (e.get("to") or "").strip()
        if not frm or not to or frm == to:
            continue
        steps = json.dumps([str(s) for s in (e.get("steps") or [])], ensure_ascii=False)
        row = conn.execute(
            'SELECT frm FROM area_edges WHERE frm=? AND "to"=?', (frm, to)
        ).fetchone()
        conn.execute(
            'INSERT OR REPLACE INTO area_edges (frm, "to", steps) VALUES (?,?,?)',
            (frm, to, steps),
        )
        if row is None:
            added += 1
        else:
            updated += 1
    conn.commit()
    return {"added": added, "updated": updated}


def list_areas(conn: sqlite3.Connection, query: str = "", limit: int = 200) -> list[dict]:
    rows = conn.execute("SELECT * FROM areas ORDER BY abbr LIMIT ?", (limit,)).fetchall()
    areas = [dict(r) for r in rows]
    if query:
        q = query.strip()
        areas = [a for a in areas
                 if q in a["abbr"] or q in (a["name_en"] or "") or q in (a["name_ch"] or "")]
    return areas


def get_area(conn: sqlite3.Connection, abbr: str):
    row = conn.execute("SELECT * FROM areas WHERE abbr=?", (abbr,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    neighbors = conn.execute(
        'SELECT "to" FROM area_edges WHERE frm=?', (abbr,)
    ).fetchall()
    d["neighbors"] = [r["to"] for r in neighbors]
    return d


def search_rooms(conn: sqlite3.Connection, name: str, limit: int = 20) -> list[dict]:
    """按 name 模糊查询 (新表)。"""
    like = f"%{name}%"
    rows = conn.execute(
        "SELECT room_id, name, area, updated_at FROM rooms WHERE name LIKE ? "
        "ORDER BY updated_at DESC LIMIT ?",
        (like, min(limit, MAX_QUERY)),
    ).fetchall()
    return [dict(r) for r in rows]


def get_room(conn: sqlite3.Connection, name: str):
    """按 name 查最新一条 + 它的 exits。"""
    row = conn.execute(
        "SELECT * FROM rooms WHERE name=? ORDER BY updated_at DESC LIMIT 1",
        (name,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    for k in ("exits_json", "objects"):
        try:
            d[k] = json.loads(d[k] or "[]" if k == "objects" else d[k] or "[]")
        except json.JSONDecodeError:
            d[k] = []
    # exits
    rows = conn.execute(
        "SELECT dir, to_room_id, source, verified_count FROM exits WHERE room_id=?",
        (d["room_id"],),
    ).fetchall()
    d["exits_table"] = [dict(r) for r in rows]
    # 拼回房间名
    d["exits"] = json.loads(d.get("exits_json") or "[]")
    d.pop("exits_json", None)
    return d


def search_npcs(conn: sqlite3.Connection, name: str, limit: int = 20) -> list[dict]:
    """在 rooms.objects 中模糊匹配。"""
    like = f"%{name}%"
    rows = conn.execute(
        "SELECT room_id, name, objects, updated_at FROM rooms "
        "WHERE objects LIKE ? OR name LIKE ? ORDER BY updated_at DESC LIMIT ?",
        (like, like, min(limit * 5, MAX_QUERY * 2)),
    ).fetchall()
    result = []
    for r in rows:
        try:
            objs = json.loads(r["objects"] or "[]")
        except json.JSONDecodeError:
            objs = []
        hits = [o for o in objs if name in (o.get("name") or "") or name in (o.get("id") or "")]
        if hits:
            result.append({
                "room_id": r["room_id"],
                "room": r["name"],
                "npc": hits[0].get("name") or "",
                "npc_id": hits[0].get("id") or "",
                "updated_at": r["updated_at"],
            })
            if len(result) >= limit:
                break
    return result


def find_route(conn: sqlite3.Connection, start_id: str, target_id: str):
    """基于 exits 表 BFS 寻路 (按 room_id)。
    返回每步含房间名/坐标/方向, 客户端可直接用于导航渲染。"""
    if not start_id or not target_id:
        return None
    if start_id == target_id:
        return {"from": start_id, "to": target_id, "route": [], "steps": 0, "distance": 0}

    # 加载房间元数据 (含 name/pos)
    meta: dict[str, dict] = {
        r["room_id"]: dict(r)
        for r in conn.execute(
            "SELECT room_id, name, area, pos_x, pos_y FROM rooms"
        ).fetchall()
    }
    if start_id not in meta or target_id not in meta:
        return {"from": start_id, "to": target_id, "route": None,
                "error": "unknown_room"}

    graph: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in conn.execute("SELECT room_id, dir, to_room_id FROM exits WHERE to_room_id IS NOT NULL"):
        graph[row["room_id"]].append((row["dir"], row["to_room_id"]))
    prev: dict[str, tuple[str, str]] = {}
    visited = {start_id}
    queue = [start_id]
    while queue:
        cur = queue.pop(0)
        for d, nxt in graph.get(cur, []):
            if nxt in visited:
                continue
            prev[nxt] = (cur, d)
            if nxt == target_id:
                route = []
                node = target_id
                while node != start_id:
                    p_room, p_dir = prev[node]
                    m = meta[node]
                    route.append({
                        "from": p_room,
                        "from_name": meta[p_room]["name"],
                        "dir": p_dir,
                        "to": node,
                        "to_name": m["name"],
                        "to_pos": [m["pos_x"], m["pos_y"]],
                    })
                    node = p_room
                route.reverse()
                # 估算总距离 (从房间坐标累加)
                distance = 0
                if len(route) >= 1:
                    prev_pos = (meta[start_id]["pos_x"], meta[start_id]["pos_y"])
                    for step in route:
                        p = step["to_pos"]
                        if p[0] is not None and prev_pos[0] is not None:
                            dx = p[0] - prev_pos[0]
                            dy = p[1] - prev_pos[1]
                            distance += (dx * dx + dy * dy) ** 0.5
                        prev_pos = tuple(p) if p[0] is not None else prev_pos
                return {
                    "from": start_id,
                    "from_name": meta[start_id]["name"],
                    "to": target_id,
                    "to_name": meta[target_id]["name"],
                    "route": route,
                    "steps": len(route),
                    "distance": round(distance, 1),
                }
            visited.add(nxt)
            queue.append(nxt)
    return {"from": start_id, "to": target_id, "route": None,
            "error": "no_path"}


def stats(conn: sqlite3.Connection) -> dict:
    rooms = conn.execute("SELECT COUNT(*) c FROM rooms").fetchone()["c"]
    exits_total = conn.execute("SELECT COUNT(*) c FROM exits").fetchone()["c"]
    exits_unbound = conn.execute(
        "SELECT COUNT(*) c FROM exits WHERE to_room_id IS NULL"
    ).fetchone()["c"]
    moves = conn.execute("SELECT COUNT(*) c FROM move_log").fetchone()["c"]
    npcs = conn.execute(
        "SELECT COUNT(*) c FROM rooms WHERE objects NOT IN ('', '[]')"
    ).fetchone()["c"]
    areas = conn.execute("SELECT COUNT(*) c FROM areas").fetchone()["c"]
    area_edges = conn.execute("SELECT COUNT(*) c FROM area_edges").fetchone()["c"]
    return {
        "rooms": rooms,
        "exits": exits_total,
        "exits_unbound": exits_unbound,
        "moves": moves,
        "rooms_with_npcs": npcs,
        "areas": areas,
        "area_edges": area_edges,
    }


def export_all(conn: sqlite3.Connection) -> dict:
    """全量导出 (含 exits)。"""
    rooms = [dict(r) for r in conn.execute("SELECT * FROM rooms").fetchall()]
    for r in rooms:
        try:
            r["exits_list"] = json.loads(r.pop("exits_json") or "[]")
        except json.JSONDecodeError:
            r["exits_list"] = []
        try:
            r["objects"] = json.loads(r["objects"] or "[]")
        except json.JSONDecodeError:
            r["objects"] = []
    exits = [
        {
            "room_id": row["room_id"],
            "dir": row["dir"],
            "to_room_id": row["to_room_id"],
            "source": row["source"],
            "verified_count": row["verified_count"],
        }
        for row in conn.execute(
            "SELECT room_id, dir, to_room_id, source, verified_count FROM exits"
        ).fetchall()
    ]
    # 2026-08-04 16:53: 兼容旧客户端 — 同步输出 edges (name 形式)
    # 老 map_cache.py 期望 edges = [[frm_name, dir, to_name], ...]
    id_to_name = {r["room_id"]: r["name"] for r in rooms}
    edges = []
    for e in exits:
        frm_name = id_to_name.get(e["room_id"], e["room_id"])
        to_name = id_to_name.get(e["to_room_id"], e["to_room_id"]) if e["to_room_id"] else None
        # 2026-08-04 18:10: 过滤自指 (room_id == to_room_id) — 客户端 v3.0 修复后不该再产生,
        # 但可能存在历史脏数据 (例如 v3.0 修复前老 EXE 推上来的)
        if frm_name == to_name:
            continue
        edges.append([frm_name, e["dir"], to_name])
    return {
        "exported_at": time.time(),
        "schema_version": 2,
        "rooms": rooms,
        "exits": exits,
        "edges": edges,  # 同步给客户端画图
    }


# ── HTTP ──────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "XkxMapServer/2.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)
        conn = db_connect()
        try:
            if path == "/api/map/stats":
                self.send_json(stats(conn))
            elif path == "/api/map/rooms":
                name = q.get("name", [""])[0].strip()
                limit = int(q.get("limit", ["20"])[0] or 20)
                self.send_json({"rooms": search_rooms(conn, name, limit)})
            elif path.startswith("/api/map/rooms/"):
                name = unquote(path[len("/api/map/rooms/"):])
                room = get_room(conn, name)
                if room is None:
                    self.send_json({"room": None}, code=404)
                else:
                    self.send_json({"room": room})
            elif path == "/api/map/npcs":
                name = q.get("name", [""])[0].strip()
                limit = int(q.get("limit", ["20"])[0] or 20)
                self.send_json({"npcs": search_npcs(conn, name, limit)})
            elif path == "/api/map/route":
                frm = q.get("from", [""])[0].strip()
                to = q.get("to", [""])[0].strip()
                self.send_json(find_route(conn, frm, to))
            elif path == "/api/map/areas":
                query = q.get("q", [""])[0].strip()
                limit = int(q.get("limit", ["200"])[0] or 200)
                full = q.get("full", ["0"])[0].strip() == "1"
                resp = {"areas": list_areas(conn, query, limit)}
                if full:
                    rows = conn.execute(
                        'SELECT frm, "to", steps FROM area_edges'
                    ).fetchall()
                    resp["edges"] = [
                        {"frm": r["frm"], "to": r["to"], "steps": json.loads(r["steps"] or "[]")}
                        for r in rows
                    ]
                self.send_json(resp)
            elif path.startswith("/api/map/areas/"):
                abbr = unquote(path[len("/api/map/areas/"):]).strip()
                area = get_area(conn, abbr)
                if area is None:
                    self.send_json({"area": None}, code=404)
                else:
                    self.send_json({"area": area})
            elif path == "/api/map/export":
                self.send_json(export_all(conn))
            elif path == "/api/user/list":
                code, payload = handle_get_user(q, path)
                self.send_json(payload, code=code)
                return
            elif path.startswith("/api/macros/"):
                code, payload = handle_get_macros(q, path)
                self.send_json(payload, code=code)
                return
            else:
                self.send_json({"error": "not found"}, code=404)
        finally:
            conn.close()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "bad json"}, code=400)
            return
        conn = db_connect()
        try:
            if path == "/api/map/areas":
                r1 = upsert_areas(conn, body.get("areas") or [])
                r2 = upsert_area_edges(conn, body.get("edges") or [])
                self.send_json({"ok": True, "areas": r1, "edges": r2,
                                "stats": stats(conn)})
                return

            if path == "/api/map/moves":
                # 新协议: {client_id, ts, moves, exits, rooms}
                # 处理顺序: rooms -> exits -> moves (FK 约束要求 rooms 先 INSERT,
                # 不然 moves/exits 引用的 room_id 还没建)
                client = (body.get("client_id") or "unknown")[:64]
                ts = float(body.get("ts") or time.time())
                moves = body.get("moves") or []
                exits_list = body.get("exits") or []
                rooms = body.get("rooms") or []

                # 1) rooms 先 upsert (服务端派生真 ID, 客户端第一次推时 room_id="")
                rooms_added = 0
                rooms_updated = 0
                room_ids: dict[str, str] = {}
                for r in rooms:
                    rid = upsert_room(conn, r, client)
                    if rid:
                        room_ids[r.get("name") or ""] = rid
                conn.commit()  # rooms 立即落库, 让后续 moves/exits 可引用

                # 2026-08-04 14:59: 补充从服务端 rooms 表查已有 name→room_id 映射
                # 客户端可能上一帧推了 name 但本帧只推 exits 没传 rooms,
                # 这里补齐映射, 让 exits/moves 能用真 ID 引用
                if exits_list or moves:
                    existing = conn.execute(
                        "SELECT name, room_id FROM rooms"
                    ).fetchall()
                    for row in existing:
                        n = row["name"]
                        if n and n not in room_ids:
                            room_ids[n] = row["room_id"]

                def _resolve_room_id(token: str) -> str:
                    """解析房间 token:
                    - 空串 → 空串
                    - "local:<name>" → 查 room_ids[name], 找不到则 SELECT rooms 表
                    - 纯 name → 查 room_ids[name], 找不到则 SELECT rooms 表
                    - 已是真 room_id → 原值
                    返回 真 rid (永远不会是 local:xxx)
                    """
                    if not token:
                        return token
                    if token.startswith("local:"):
                        name = token[len("local:"):]
                    else:
                        name = token
                    # 1. 本批次 room_ids
                    if name in room_ids:
                        return room_ids[name]
                    # 2. 2026-08-04 16:40: 修 — fallback 到 rooms 表 SELECT
                    # 客户端发 local:xxx 时,如果该房间之前已推过但本批次没推
                    # → 仍能在 rooms 表找到最新 rid
                    if name:
                        row = conn.execute(
                            "SELECT room_id FROM rooms WHERE name=? "
                            "ORDER BY updated_at DESC LIMIT 1",
                            (name,),
                        ).fetchone()
                        if row:
                            return row["room_id"]
                    return token

                # 2) exits 批量 (含悬空 to_id=None, 服务端先 upsert room_id 引用)
                # 2026-08-04 14:48 包 try/except 防御 FK 失败(不应发生但 upsert_exit 内部已预检+降级)
                # 2026-08-04 14:59 先用 _resolve_room_id 替换 local: 占位为真 ID
                exits_results = []
                for e in exits_list:
                    rid_raw = (e.get("room_id") or "").strip()
                    if not rid_raw:
                        continue
                    rid = _resolve_room_id(rid_raw)
                    to_id_raw = (e.get("to_id") or "").strip()
                    to_id = _resolve_room_id(to_id_raw) if to_id_raw else None
                    # 改写 exit 用真 ID
                    e_resolved = dict(e)
                    e_resolved["room_id"] = rid
                    e_resolved["to_id"] = to_id
                    try:
                        r = apply_exits(conn, rid, [e_resolved], client)
                        exits_results.append(r)
                    except sqlite3.IntegrityError as ex:
                        # FK 失败不应再发生 (upsert_exit 已预检), 仅作最后防线
                        print(f"[warn] do_POST exits FK 失败: room_id={rid} ex={ex}", file=sys.stderr, flush=True)
                        conn.rollback()
                        exits_results.append({"skipped": 1, "fk_error": str(ex)})

                # 3) moves 最后处理 (frm_id/to_id 是真 ID 时才能通过 FK)
                # 2026-08-04 14:48 包 try/except 防御 FK 失败
                # 2026-08-04 14:59 先用 _resolve_room_id 替换 local: 占位为真 ID
                move_results = []
                for m in moves:
                    frm_raw = (m.get("frm_id") or "").strip()
                    d = (m.get("dir") or "").strip()
                    to_raw = (m.get("to_id") or "").strip()
                    d_norm = normalize_dir(d)
                    frm_id = _resolve_room_id(frm_raw)
                    to_id = _resolve_room_id(to_raw)
                    if not (frm_id and to_id and d_norm in DIRS_REVERSE):
                        move_results.append({"status": "skipped",
                                            "reason": "invalid",
                                            "frm": frm_id, "to": to_id, "dir": d,
                                            "d_norm": d_norm})
                        continue
                    conn.commit()  # 提交前一个 move 的写入
                    try:
                        move_results.append(apply_move(conn, frm_id, d, to_id, ts, client))
                    except sqlite3.IntegrityError as ex:
                        # FK 失败降级为 skipped (frm_id/to_id 可能服务端还没收到该房间)
                        print(f"[warn] do_POST move FK 失败: frm={frm_id} to={to_id} ex={ex}", file=sys.stderr, flush=True)
                        conn.rollback()
                        move_results.append({"status": "skipped", "reason": "fk_error",
                                            "frm": frm_id, "to": to_id, "dir": d,
                                            "err": str(ex)})

                self.send_json({
                    "ok": True,
                    "client_id": client,
                    "moves": {"count": len(moves), "results": move_results[:20]},
                    "exits": {"count": len(exits_list)},
                    "rooms": {"count": len(rooms), "assigned_ids": room_ids},
                    "stats": stats(conn),
                })
                return

            if path == "/api/map/rooms":
                # 旧协议 (v1 兼容): {rooms, edges, client_id}
                client = (body.get("client_id") or "unknown")[:64]
                rooms = body.get("rooms") or []
                edges = body.get("edges") or []
                for r in rooms:
                    upsert_room(conn, r, client)
                # 旧 edges: 视为 moves
                for e in edges:
                    if len(e) != 3:
                        continue
                    frm_name, d, to_name = (str(e[0]).strip(), str(e[1]).strip(), str(e[2]).strip())
                    if d not in VALID_DIRS:
                        continue
                    # 用 name 找最新 room_id
                    frm_row = conn.execute(
                        "SELECT room_id FROM rooms WHERE name=? ORDER BY updated_at DESC LIMIT 1",
                        (frm_name,),
                    ).fetchone()
                    to_row = conn.execute(
                        "SELECT room_id FROM rooms WHERE name=? ORDER BY updated_at DESC LIMIT 1",
                        (to_name,),
                    ).fetchone()
                    if frm_row and to_row:
                        apply_move(conn, frm_row["room_id"], d, to_row["room_id"], time.time(), client)
                conn.commit()
                self.send_json({
                    "ok": True,
                    "client_id": client,
                    "deprecated": True,
                    "note": "use /api/map/moves",
                    "stats": stats(conn),
                })
                return

            if path == "/api/user/register" or path == "/api/user/login" or path.startswith("/api/user/"):
                code, payload = handle_post_user(path, body)
                self.send_json(payload, code=code)
                return

            if path == "/api/macros/delete":
                # 删除宏归属校验：token 对应账号 == 宏归属
                token = str(body.get("token") or "")
                uid = _check_token(token)
                if uid is None:
                    self.send_json({"ok": False, "error": "登录已失效，请重新登录"}, code=401)
                    return
                body["owner"] = uid
                code, payload = handle_post_macros(path, body)
                self.send_json(payload, code=code)
                return

            if path.startswith("/api/macros/"):
                code, payload = handle_post_macros(path, body)
                self.send_json(payload, code=code)
                return

            self.send_json({"error": "not found"}, code=404)
        finally:
            conn.close()

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
    init_db()
    print(f"[map_server v2] listening on 0.0.0.0:{PORT} db={DB_PATH}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()