# 用全部探针的实例数据重建实例模型 map_cache.json。
# 1) 身份合并：同 name + 同 exits 集 -> 同一实例；同 name 不同 exits -> 独立实例。
# 2) 坐标对齐：探针各自是相对网格(起点 0,0,0 + _DIR_DELTA 累加)。用跨探针共享实例
#    作为锚点求解每个探针的偏移，连通探针组共享同一坐标系（全部连通即全局巨型坐标系，
#    不连通则按区域各一套坐标系）。实例全局坐标 = 各探针样本坐标+探针偏移 的众数。
# 3) 边双向补全（镜像方向），方向冲突保留已有。
# 输出 nodes/name_to_ids/current_id + 名字键 rooms（含探针 room_records 的 desc/npc 元数据）。

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

OPPOSITE = {
    "north": "south", "south": "north", "east": "west", "west": "east",
    "northup": "southdown", "southdown": "northup", "southup": "northdown", "northdown": "southup",
    "eastup": "westdown", "westdown": "eastup", "westup": "eastdown", "eastdown": "westup",
    "northeast": "southwest", "southwest": "northeast",
    "northwest": "southeast", "southeast": "northwest",
    "up": "down", "down": "up", "enter": "out", "out": "enter",
}


def is_zero(c) -> bool:
    return not (isinstance(c, (list, tuple)) and any(bool(v) for v in c))


def load_probe_files() -> list[Path]:
    temp = Path(os.environ.get("TEMP", str(Path.home()))) / "opencode"
    files = sorted(p for p in temp.glob("*.rooms.json") if not p.name.endswith(".norm.json"))
    root = Path(os.environ.get("EASYBXB_ROOT", r"D:\BaiduSyncdisk\EasyBXb"))
    for f in ("hh11.bin.rooms.json", "hh12.bin.rooms.json", "hh13.bin.rooms.json"):
        p = root / f
        if p.exists() and p not in files:
            files.append(p)
    return files


def main() -> None:
    files = load_probe_files()
    if not files:
        print("no probe files")
        sys.exit(1)

    key_to_id: dict[tuple, str] = {}
    instances: dict[str, dict] = {}
    rooms_meta: dict[str, dict] = {}
    nseq = 0
    conflicts = 0
    edge_count = 0

    # 每探针：key -> 首个有效坐标
    probe_samples: list[dict[tuple, tuple]] = []
    # key -> 出现过的探针下标
    key_probes: dict[tuple, list[int]] = defaultdict(list)

    def get_id(key: tuple) -> str:
        nonlocal nseq
        nid = key_to_id.get(key)
        if nid is not None:
            return nid
        nid = "n%d" % nseq
        nseq += 1
        key_to_id[key] = nid
        instances[nid] = {
            "name": key[0], "exits": set(key[1]),
            "coords": None, "coords_list": [], "neighbors": {},
        }
        return nid

    probe_idx = 0
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print("skip %s: %s" % (f.name, e))
            continue
        nodes = data.get("nodes") or []
        if isinstance(nodes, dict):
            nodes = list(nodes.values())
        kof: dict[str, tuple] = {}
        for nd in nodes:
            if not isinstance(nd, dict) or not nd.get("name"):
                continue
            kof[nd.get("id")] = (nd["name"], frozenset(nd.get("exits") or []))
        for key in kof.values():
            get_id(key)
        samples: dict[tuple, tuple] = {}
        for nd in nodes:
            key = kof.get(nd.get("id"))
            if not key:
                continue
            c = nd.get("coords")
            if not is_zero(c):
                c = tuple(c)
                if key not in samples:
                    samples[key] = c
                key_probes[key].append(probe_idx)
        probe_samples.append(samples)
        for nd in nodes:
            ka = kof.get(nd.get("id"))
            if not ka:
                continue
            ia = key_to_id[ka]
            for d2, tgt in (nd.get("neighbors") or {}).items():
                if not isinstance(tgt, dict) or d2 in ("<SUPPORT>",):
                    continue
                tb = tgt.get("to")
                tbname = tgt.get("name")
                kb = kof.get(tb) if tb and tb in kof else (tbname or "", frozenset())
                if not kb[0]:
                    continue
                ib = get_id(kb)
                nb = instances[ia]["neighbors"]
                if d2 in nb and nb[d2] != ib:
                    conflicts += 1
                    continue
                nb[d2] = ib
                edge_count += 1
                op = OPPOSITE.get(d2)
                if op:
                    rb = instances[ib]["neighbors"]
                    if op in rb and rb[op] != ia:
                        conflicts += 1
                    else:
                        rb[op] = ia
        for rid, rec in (data.get("room_records") or {}).items():
            if not isinstance(rec, dict) or not rec.get("name"):
                continue
            meta = rooms_meta.setdefault(rec["name"], {})
            for s in (rec.get("npc") or []):
                if isinstance(s, list) and len(s) >= 2:
                    meta.setdefault("npc", []).append({"name": s[0], "id": s[1]})
                elif isinstance(s, str):
                    meta.setdefault("npc", []).append({"name": s})
            for s in (rec.get("desc") or []):
                if isinstance(s, str) and s not in meta.setdefault("desc", []):
                    meta["desc"].append(s)
        probe_idx += 1

    # ---- Phase 2: 探针偏移对齐（共享 key 提供约束，BFS/投票传播）----
    n_probes = probe_idx
    offsets: list[tuple | None] = [None] * n_probes
    votes: list[Counter] = [Counter() for _ in range(n_probes)]
    root = 0
    offsets[root] = (0, 0, 0)
    done = {root}
    pending = set(range(n_probes)) - done
    queue = [root]
    while queue and pending:
        p = queue.pop(0)
        for key in probe_samples[p]:
            cp = probe_samples[p][key]
            for q in key_probes[key]:
                if q not in pending or q not in probe_samples:
                    continue
                cq = probe_samples[q][key]
                off = (offsets[p][0] + cp[0] - cq[0],
                       offsets[p][1] + cp[1] - cq[1],
                       offsets[p][2] + cp[2] - cq[2])
                votes[q][off] += 1
        # 结算本轮已获得足够支持的探针
        advanced = True
        while advanced:
            advanced = False
            for q in list(pending):
                if votes[q]:
                    off, cnt = votes[q].most_common(1)[0]
                    # 至少有共享锚点约束
                    offsets[q] = off
                    pending.discard(q)
                    done.add(q)
                    queue.append(q)
                    advanced = True
    for q in pending:
        offsets[q] = (0, 0, 0)  # 独立区域（无任何共享锚点）

    # ---- Phase 3: 实例全局坐标（各探针样本坐标+偏移 的众数）----
    coord_samples: dict[str, Counter] = defaultdict(Counter)
    for p, samples in enumerate(probe_samples):
        off = offsets[p]
        for key, c in samples.items():
            nid = key_to_id[key]
            gc = (c[0] + off[0], c[1] + off[1], c[2] + off[2])
            coord_samples[nid][gc] += 1
    for nid, cnt in coord_samples.items():
        gc, n = cnt.most_common(1)[0]
        instances[nid]["coords"] = list(gc)
        instances[nid]["coords_list"].append(list(gc))

    # ---- Phase 3.5: 同 name 实例合并决策 ----
    # 同 name 有坐标且坐标不同 -> 保留多实例（真正重名，导航时由近到远选择）；
    # 同 name 同坐标或无坐标 -> 合一（exits/neighbors 并集），保证跨探针连通性。
    name_ids: dict[str, list[str]] = defaultdict(list)
    for nid, inst in instances.items():
        name_ids[inst["name"]].append(nid)

    def _merge_two(rep: str, other: str) -> None:
        nonlocal conflicts
        ri = instances[rep]
        oi = instances[other]
        ri["exits"] |= oi["exits"]
        if ri["coords"] is None:
            ri["coords"] = oi["coords"]
        for c in oi["coords_list"]:
            if c not in ri["coords_list"]:
                ri["coords_list"].append(c)
        # rep 指向 other 的内部边删除
        for d in [d for d, t in ri["neighbors"].items() if t == other]:
            del ri["neighbors"][d]
        for d, t in oi["neighbors"].items():
            if t == rep:
                continue
            if d in ri["neighbors"] and ri["neighbors"][d] != t:
                conflicts += 1
                continue
            ri["neighbors"][d] = t
        del instances[other]
        # 其余实例对 other 的引用重定向到 rep
        for nd in instances.values():
            for d, t in list(nd["neighbors"].items()):
                if t == other:
                    nd["neighbors"][d] = rep

    for name, nids in name_ids.items():
        if len(nids) <= 1:
            continue
        buckets: dict = {}
        for nid in nids:
            c = instances[nid]["coords"]
            buckets.setdefault(tuple(c) if c else None, []).append(nid)
        if len(buckets) == 1:
            rep = nids[0]
            for other in nids[1:]:
                _merge_two(rep, other)
            continue
        reps = {}
        for key, group in buckets.items():
            g0 = group[0]
            for other in group[1:]:
                _merge_two(g0, other)
            reps[key] = g0
        if None in buckets and reps.get(None) and any(k is not None for k in reps):
            best = max((k for k in reps if k is not None),
                       key=lambda k: len(instances[reps[k]]["neighbors"]))
            _merge_two(reps[best], reps[None])

    # ---- Phase 4: 输出 ----
    nodes_out = {}
    name_to_ids: dict[str, list[str]] = {}
    rooms: dict[str, dict] = {}
    for nid, inst in instances.items():
        nodes_out[nid] = {
            "name": inst["name"],
            "exits": sorted(inst["exits"]),
            "coords": inst["coords"],
            "coords_list": inst["coords_list"],
            "neighbors": dict(inst["neighbors"]),
        }
        name_to_ids.setdefault(inst["name"], []).append(nid)
        r = rooms.setdefault(inst["name"], {"exits": []})
        r["exits"] = list(dict.fromkeys([*r["exits"], *sorted(inst["exits"])]))
        if "coords" not in r and inst["coords"]:
            r["coords"] = list(inst["coords"])
    for name, meta in rooms_meta.items():
        r = rooms.setdefault(name, {"exits": []})
        for k in ("desc", "npc"):
            if meta.get(k):
                r.setdefault(k, []).extend(meta[k])

    target = Path(os.environ["APPDATA"]) / "XkxClient" / "config" / "map_cache.json"
    backup = target.with_name("map_cache.namemodel.json")
    if target.exists() and not backup.exists():
        target.replace(backup)
    # 名字键导航骨架：优先用备份的名字键模型 edges（实例化边冲突多、连通性不可靠）
    edges = {}
    if backup.exists():
        try:
            bd = json.loads(backup.read_text(encoding="utf-8"))
            edges = bd.get("edges") or {}
        except Exception:
            edges = {}
    if not edges:
        for nid, nd in instances.items():
            for d, t in nd["neighbors"].items():
                edges.setdefault(nd["name"], {})[d] = instances[t]["name"]
    out = {
        "nodes": nodes_out,
        "name_to_ids": name_to_ids,
        "current_id": "",
        "rooms": rooms,
        "edges": edges,
        "current": "",
    }
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)

    n_same = sum(1 for v in name_to_ids.values() if len(v) > 1)
    n_coord = sum(1 for nd in nodes_out.values() if nd["coords"])
    groups = {}
    for p, off in enumerate(offsets):
        groups.setdefault(off, 0)
    print("probe files=%d -> instances=%d, names=%d (multi-instance names=%d), "
          "coords=%d, edges=%d, conflicts=%d, coord_groups=%d"
          % (len(files), len(nodes_out), len(name_to_ids), n_same, n_coord,
             edge_count, conflicts, len(groups)))
    print("backup -> %s" % backup)
    print("written -> %s" % target)


if __name__ == "__main__":
    main()