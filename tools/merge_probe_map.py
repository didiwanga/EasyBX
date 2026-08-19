# 把探针采集的 .rooms.json（实例模型）合并进客户端 map_cache.json（名字键模型）。
# 用法: python tools/merge_probe_map.py <probe.json>... [--map-cache <path>] [--drop-existing-edges]
# 兼容差异：instance -> name 键；同名多实例的 exits 取并集、坐标收进 coords_list，
# 同名同方向映射冲突时保留已存在边并记录到 edge_conflicts（供后续实例化改造）。
# --drop-existing-edges：抛弃目标文件中已有的旧 edges（旧名字键数据常因同名串边不可靠），
# 只保留旧 rooms（名字/出口/元数据）作为知识库，edges 由探针数据重建。

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    argv = list(sys.argv[1:])
    mc_path = None
    drop_edges = False
    if "--drop-existing-edges" in argv:
        drop_edges = True
        argv.remove("--drop-existing-edges")
    if "--map-cache" in argv:
        i = argv.index("--map-cache")
        mc_path = Path(argv[i + 1])
        del argv[i:i + 2]
    if not argv:
        print("usage: merge_probe_map.py <probe.json>... [--map-cache <path>] [--drop-existing-edges]")
        sys.exit(1)
    if mc_path is None:
        mc_path = Path(os.environ.get("APPDATA", str(Path.home()))) / "XkxClient" / "config" / "map_cache.json"

    data = load(mc_path) if mc_path.exists() else {}
    rooms: dict[str, dict] = data.get("rooms", {}) or {}
    edges: dict[str, dict[str, str]] = {} if drop_edges else (data.get("edges", {}) or {})
    current = data.get("current", "") or ""

    stats = {"files": 0, "nodes": 0, "new_rooms": 0, "conflicts": 0}
    conflict_set: set[tuple[str, str, str]] = set()

    for f in argv:
        p = load(Path(f))
        stats["files"] += 1
        nodes = p.get("nodes", []) or []
        stats["nodes"] += len(nodes)

        for node in nodes:
            name = node.get("name", "")
            if not name:
                continue
            was_new = name not in rooms
            r = rooms.setdefault(name, {"exits": []})
            if was_new:
                stats["new_rooms"] += 1

            exits = [e for e in (node.get("exits") or []) if e]
            if exits:
                cur = r.get("exits", []) or []
                r["exits"] = list(dict.fromkeys([*cur, *exits]))

            coords = node.get("coords")
            if coords:
                lst = r.setdefault("coords_list", [])
                if coords not in lst:
                    lst.append(coords)

            for d, nb in (node.get("neighbors") or {}).items():
                to_name = (nb or {}).get("name", "")
                if not d or not to_name:
                    continue
                e = edges.setdefault(name, {})
                if d not in e:
                    e[d] = to_name
                elif e[d] != to_name:
                    key = (name, d, to_name)
                    if key not in conflict_set:
                        conflict_set.add(key)
                        stats["conflicts"] += 1
                        r.setdefault("edge_conflicts", []).append({
                            "dir": d, "to": to_name, "existing": e[d],
                        })

    # 主坐标：取 coords_list 众数（无众数取第一个），供渲染/导航；coords_list 保留给实例化
    for r in rooms.values():
        cl = r.get("coords_list") or []
        if len(cl) == 1:
            r["coords"] = cl[0]
        elif len(cl) > 1:
            best = max(set(tuple(c) for c in cl), key=lambda c: sum(1 for x in cl if tuple(x) == c))
            r["coords"] = list(best)

    # 备份旧文件再写回
    if mc_path.exists():
        bak = mc_path.with_suffix(".json.bak")
        mc_path.replace(bak)
    dump(mc_path, {
        "rooms": rooms, "edges": edges, "current": current,
    })

    print("merged %d files, %d nodes -> rooms=%d(+%d), edges=%d, conflicts=%d" % (
        stats["files"], stats["nodes"], len(rooms), stats["new_rooms"],
        len(edges), stats["conflicts"]))

    report = Path(os.environ.get("TEMP", ".")) / "merge_probe_map_report.txt"
    lines = [
        "probe files: %s" % ", ".join(argv),
        "rooms: %d (new %d), edges: %d, conflicts: %d" % (
            len(rooms), stats["new_rooms"], len(edges), stats["conflicts"]),
        "current: %s" % current,
        "",
        "== edge_conflicts ==",
    ]
    for (name, d, to) in sorted(conflict_set):
        lines.append("  %s [%s] -> %s" % (name, d, to))
    lines.append("")
    lines.append("== 多坐标同名房间 (coords_list>1) ==")
    for name, r in sorted(rooms.items()):
        cl = r.get("coords_list") or []
        if len(cl) > 1:
            lines.append("  %s: %d 处坐标 %s" % (name, len(cl), cl))
    report.write_text("\n".join(lines), encoding="utf-8")
    print("report -> %s" % report)


if __name__ == "__main__":
    main()