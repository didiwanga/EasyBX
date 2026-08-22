"""地图数据上下端同步客户端（对应服务端 server/map_sync_server.py）。

流程（与现有 macroshare/client_user 同款 urllib 公网直连模式）：
    - 登录后拉全量：get_snapshot(0) → apply_snapshot 合并进本地 MapCache
    - 行走/采集后节流上报：export_changes 增量 → post_report
    - 定时拉增量（60s）：get_snapshot(last_rev) → apply_snapshot

身份合并规则与本地 _resolve_node 一致；服务端是权威主库，本地应用远端
快照时不覆盖本地已有真实坐标/边，只补充缺失数据（保守合并）。

配置：
    map.sync_enable (bool, 默认 True)
    map.sync_url    (str, 默认 http://pytools.cloud/api/map) —— 空则禁用
    map.sync_interval (秒, 默认 60)
    map.sync_throttle (秒, 上报节流, 默认 5)
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
import uuid

from xkxclient.core.config import ConfigManager
from xkxclient.core.map import _norm_dir
from xkxclient.version import MAP_SYNC_BASE, MAP_SYNC_TOKEN

_DEFAULT_BASE = MAP_SYNC_BASE
_TIMEOUT = 30

# 上报分片：单批最多实例数 / 单次 push 最多批次（300*20=6000，超出留待下次）
_PUSH_BATCH = 300
_PUSH_MAX_BATCHES = 20


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


class MapSyncError(RuntimeError):
    """地图同步网络/服务器错误。"""


def _read_json(req) -> dict:
    try:
        with _opener().open(req, timeout=_TIMEOUT) as r:
            raw = r.read()
            text = raw.decode("utf-8-sig", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8-sig", errors="replace")
        msg = ""
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict) and parsed.get("error"):
                msg = parsed["error"]
        except (ValueError, TypeError):
            pass
        raise MapSyncError(msg or f"服务器返回 HTTP {exc.code}") from exc
    except OSError as exc:
        raise MapSyncError(f"网络错误：{exc}") from exc
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise MapSyncError("服务器响应不是有效 JSON（地图同步服务可能未部署）") from exc
    return data


class MapSync:
    """地图同步客户端。绑定一个 MapCache，负责上报/拉取/合并。"""

    def __init__(self, cache, account: str = "", client_id: str = "",
                 on_status=None) -> None:
        self.cache = cache
        self.account = account
        self.client_id = client_id or _load_client_id()
        self.last_rev = 0
        self._lock = threading.RLock()
        self._last_push = 0.0
        self.enabled = self._config_bool("map.sync_enable", True)
        self.base = self._config_str("map.sync_url", _DEFAULT_BASE)
        self.status_ok: bool | None = None
        self.last_error = ""
        self._on_status = on_status
        self.server_nodes: int | None = None    # 服务器主库实例总数（get_stats 刷新）
        self.server_rooms: int | None = None    # 服务器主库去重房间名总数

    # ---- 配置 ----
    def _config_bool(self, path: str, default: bool) -> bool:
        v = ConfigManager.instance().get(path)
        return default if v is None else bool(v)

    def _config_str(self, path: str, default: str) -> str:
        return str(ConfigManager.instance().get(path) or default or "").strip()

    @property
    def active(self) -> bool:
        return bool(self.enabled and self.base)

    def _set_status(self, ok: bool, error: str = "") -> None:
        with self._lock:
            self.status_ok = ok
            self.last_error = error
        cb = self._on_status
        if cb is not None:
            try:
                cb(ok, error)
            except Exception:
                pass

    # ---- 导出本地增量（分片）----
    def _export_batch(self, limit: int = _PUSH_BATCH,
                      exclude_nodes: set | None = None,
                      exclude_rooms: set | None = None) -> dict:
        """导出待上报变更中的一批（单批上限 limit），并顺带清除已失效的僵尸实例标记。

        由 MapCache 内部标记 changed 节点；这里构造上报 payload。
        exclude_nodes/exclude_rooms：本轮预导出、尚未 commit 的 id（防重复取同一批）。
        """
        with self._lock:
            changed = set(getattr(self.cache, "changed_ids", set()))
            changed_rooms = set(getattr(self.cache, "changed_rooms", set()))
        if exclude_nodes:
            changed -= exclude_nodes
        if exclude_rooms:
            changed_rooms -= exclude_rooms
        dead = {nid for nid in changed if nid not in self.cache.nodes}
        if dead:
            with self._lock:
                self.cache.changed_ids.difference_update(dead)
            changed.difference_update(dead)
        batch = list(changed)[:limit]
        batch_rooms = list(changed_rooms)[:max(50, limit // 4)]
        nodes = {}
        for nid in batch:
            nd = self.cache.nodes.get(nid)
            if nd is None:
                continue
            nodes[nid] = {
                "name": nd["name"],
                "exits": sorted(nd["exits"]),
                "coords": nd["coords"],
                # 邻居值用名字（跨客户端可移植，服务端重链）
                "neighbors": {d: self.cache.nodes[t]["name"]
                              for d, t in nd["neighbors"].items()
                              if t in self.cache.nodes},
            }
        rooms = {}
        for name in batch_rooms:
            r = self.cache.rooms.get(name)
            if not r:
                continue
            rooms[name] = {
                "npc": list(r.get("npc", [])),
                "desc": list(r.get("desc", [])),
                "category": r.get("category", ""),
            }
        return {"nodes": nodes, "rooms": rooms}

    # ---- 上报（导出/发送/提交三段式）----
    def export_pending(self, max_batches: int = _PUSH_MAX_BATCHES) -> list[dict]:
        """导出全部待上报批次。必须在 Qt 主线程调用：
        读 cache.nodes/neighbors 与主线程行走采集并发，需互斥于事件循环。

        预导出模式：各批互不重叠（exclude 游标），commit 在发送成功后统一进行。
        """
        out: list[dict] = []
        ex_n: set = set()
        ex_r: set = set()
        for _ in range(max(1, max_batches)):
            p = self._export_batch(exclude_nodes=ex_n, exclude_rooms=ex_r)
            if not p["nodes"] and not p["rooms"]:
                break
            ex_n |= p["nodes"].keys()
            ex_r |= p["rooms"].keys()
            out.append(p)
        return out

    @staticmethod
    def send_report(payload: dict) -> dict:
        """网络发送单批上报（可在后台线程）。返回服务端响应。"""
        body = {
            "client_id": payload["client_id"],
            "account": payload["account"],
            "nodes": payload["nodes"],
            "rooms": payload["rooms"],
        }
        req = urllib.request.Request(
            payload["base"] + "/report",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        if MAP_SYNC_TOKEN:
            req.add_header("X-Map-Token", MAP_SYNC_TOKEN)
        return _read_json(req)

    def commit(self, payload: dict, resp: dict | None) -> None:
        """上报成功后清除已上报标记并推进 revision。
        difference_update 为逐元素原子操作，可安全与主线程并发。"""
        with self._lock:
            self.cache.changed_ids.difference_update(payload["nodes"].keys())
            self.cache.changed_rooms.difference_update(payload["rooms"].keys())
        if resp:
            self.last_rev = int(resp.get("revision") or self.last_rev)

    def push(self) -> dict | None:
        """上报本地增量到服务端（分片，交错导出→发送→提交）。同线程组合接口。"""
        if not self.active:
            return None
        last = None
        for _ in range(_PUSH_MAX_BATCHES):
            payload = self._export_batch()
            if not payload["nodes"] and not payload["rooms"]:
                break
            data = self.send_report(self._with_meta(payload))
            self.commit(payload, data)
            self._set_status(True)
            last = data
        return last

    def _with_meta(self, payload: dict) -> dict:
        payload.setdefault("client_id", self.client_id)
        payload.setdefault("account", self.account)
        payload.setdefault("base", self.base)
        return payload

    # ---- 拉取 ----
    def pull(self, since: int | None = None) -> dict:
        """拉取服务端增量（since<=0 全量）。返回 {revision, nodes, rooms}。

        增量拉取带 lite=1：服务端 rooms 只回最近窗口内变更的（rooms 无
        revision 号，全量 rooms 每次都发会造成大量重复传输）。
        """
        since = self.last_rev if since is None else since
        url = f"{self.base}/snapshot?since={since}&client={self.client_id}"
        if since > 0:
            url += "&lite=1"
        req = urllib.request.Request(url, method="GET")
        if MAP_SYNC_TOKEN:
            req.add_header("X-Map-Token", MAP_SYNC_TOKEN)
        data = _read_json(req)
        self.last_rev = int(data.get("revision") or self.last_rev)
        self._set_status(True)
        return data

    # ---- 合并到本地 ----
    def apply_snapshot(self, snap: dict) -> dict:
        """把服务端快照合并进本地 MapCache（保守：只补缺失，不覆盖本地真实数据）。

        返回 {applied_nodes, applied_rooms, skipped}。
        """
        nodes = snap.get("nodes") or {}
        rooms = snap.get("rooms") or {}
        cache = self.cache
        with self._lock:
            applied_nodes = 0
            skipped = 0
            src_nid_map: dict[str, str] = {}   # 服务端 nid -> 本地 nid
            for nid, nd in nodes.items():
                if not isinstance(nd, dict):
                    continue
                name = str(nd.get("name") or "")
                if not name:
                    continue
                exits = set(nd.get("exits") or [])
                coords = nd.get("coords")
                local_nid = cache._resolve_node(name, exits, coords)
                if local_nid is None:
                    local_nid = cache._new_node(name, exits, coords)
                    # 远端数据视为已同步：撤销 _new_node 的待上报标记，避免全量
                    # 拉取后把服务器数据原样回传（ping-pong）
                    cache.changed_ids.discard(local_nid)
                    cache.changed_rooms.discard(name)
                    applied_nodes += 1
                else:
                    # 已有实例：补出口（不覆盖本地坐标，除非本地是占位且远端更真实）
                    lnd = cache.nodes[local_nid]
                    lnd["exits"] |= exits
                    if lnd["coords"] is None and coords:
                        lnd["coords"] = list(coords)
                    skipped += 1
                src_nid_map[nid] = local_nid
            # 建边：服务端 neighbors（服务端 nid）→ 本地 nid（本批快照内）
            for nid, nd in nodes.items():
                if not isinstance(nd, dict):
                    continue
                a = src_nid_map.get(nid)
                if a is None or a not in cache.nodes:
                    continue
                for d, tid in (nd.get("neighbors") or {}).items():
                    d = _norm_dir(d)
                    if not d:
                        continue
                    b = src_nid_map.get(tid)
                    if b and b in cache.nodes and b != a:
                        cache._link_ids(a, d, b)
            applied_rooms = 0
            for rname, r in rooms.items():
                if not isinstance(r, dict):
                    continue
                cur = cache.rooms.get(rname)
                if cur is None:
                    cache.rooms[rname] = {
                        "npc": list(r.get("npc", [])),
                        "desc": list(r.get("desc", [])),
                        "category": r.get("category", ""),
                    }
                    applied_rooms += 1
                else:
                    for k in ("npc", "desc"):
                        for v in r.get(k, []):
                            if v not in cur.setdefault(k, []):
                                cur[k].append(v)
            # 重建视图 + 清增量标记（远端数据视为已同步）
            cache._rebuild_views()
            cache._dirty = True
            cache.flush()
        return {"applied_nodes": applied_nodes, "applied_rooms": applied_rooms,
                "skipped": skipped}

    # ---- 统计 ----
    def get_stats(self) -> dict | None:
        """拉取服务器主库统计（实例/房间总数），缓存供状态栏实时展示。

        失败置同步状态为失败并返回 None。需在后台线程调用（网络阻塞）。
        """
        if not self.active:
            return None
        try:
            req = urllib.request.Request(self.base + "/stats", method="GET")
            data = _read_json(req)
        except MapSyncError as exc:
            self._set_status(False, str(exc))
            return None
        self._set_status(True)
        with self._lock:
            self.server_nodes = int(data.get("nodes") or 0)
            self.server_rooms = int(data.get("rooms") or 0)
        return data

    def stat_summary(self) -> dict:
        """聚合本地/服务器同步统计（状态栏展示用，不触发网络）。

        待上传 = 本地未上报的变更实例数；待下载 = 服务器比本地多的实例缺口。
        """
        cache = self.cache
        with self._lock:
            local_nodes = len(cache.nodes)
            local_rooms = len(cache.rooms)
            pending_upload = len(getattr(cache, "changed_ids", set()))
            server_nodes = self.server_nodes
        if server_nodes is None:
            pending_download = 0
        else:
            pending_download = max(0, server_nodes - local_nodes)
        if self.status_ok is None:
            status = "未同步"
        elif self.status_ok:
            status = "正常"
        else:
            status = self.last_error or "同步失败"
        return {
            "server_nodes": server_nodes,
            "server_rooms": self.server_rooms,
            "local_nodes": local_nodes,
            "local_rooms": local_rooms,
            "pending_upload": pending_upload,
            "pending_download": pending_download,
            "status_ok": self.status_ok,
            "status": status,
        }

    # ---- 定时 ----
    def tick(self) -> dict | None:
        """定时任务：先上报增量，再拉取增量。返回合并结果（无变更返回 None）。"""
        if not self.active:
            return None
        ok = True
        error = ""
        try:
            self.push()
        except MapSyncError as exc:
            ok = False
            error = str(exc)
        try:
            snap = self.pull()
            if snap.get("nodes") or snap.get("rooms"):
                return self.apply_snapshot(snap)
        except MapSyncError as exc:
            ok = False
            error = error or str(exc)
        if not ok:
            self._set_status(False, error)
        return None


def _load_client_id() -> str:
    cfg = ConfigManager.instance()
    cid = str(cfg.get("map.client_id", "") or "")
    if not cid:
        cid = uuid.uuid4().hex[:12]
        cfg.set("map.client_id", cid)
    return cid


# 共享上报令牌（随包分发，与服务端 MAP_SYNC_TOKEN 一致才允许写主库）

