# 探针房间坐标归一化：多数邻居投票迭代修正漂移（期望坐标 = 邻居坐标 - 方向增量）。
# 用法: python tools/normalize_probe_coords.py <probe.json>...
# 输出: 同目录下 <name>.norm.json，再交给 merge_probe_map.py 合并。

from __future__ import annotations

import json
import sys
from pathlib import Path

DELTA = {
    "north": (0, 1, 0), "south": (0, -1, 0),
    "east": (1, 0, 0), "west": (-1, 0, 0),
    "northeast": (1, 1, 0), "southeast": (1, -1, 0),
    "northwest": (-1, 1, 0), "southwest": (-1, -1, 0),
    "up": (0, 0, 1), "down": (0, 0, -1),
    "enter": (0, 0, 0), "out": (0, 0, 0),
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def normalize(data: dict) -> tuple[int, int]:
    nodes = data.get("nodes", []) or []
    coords: dict[str, tuple[int, int, int]] = {}
    neighbors: dict[str, dict] = {}
    for nd in nodes:
        nid = nd["id"]
        c = nd.get("coords") or [0, 0, 0]
        coords[nid] = (int(c[0]), int(c[1]), int(c[2]))
        neighbors[nid] = nd.get("neighbors") or {}

    moves = 0
    for _ in range(60):
        changed = False
        for nid in list(coords):
            votes: list[tuple[int, int, int]] = []
            for d, nb in neighbors[nid].items():
                tid = nb.get("to")
                if tid not in coords:
                    continue
                dd = DELTA.get(d, (0, 0, 0))
                votes.append((coords[tid][0] - dd[0],
                              coords[tid][1] - dd[1],
                              coords[tid][2] - dd[2]))
            if not votes:
                continue
            best = max(set(votes), key=votes.count)
            if coords[nid] != best:
                # tie-break：偏离初始坐标更大的放弃，防震荡
                init = coords[nid]
                coords[nid] = best
                changed = True
                moves += 1
        if not changed:
            break

    for nd in nodes:
        nd["coords"] = list(coords[nd["id"]])
    return moves, len(nodes)


def main() -> None:
    if not sys.argv[1:]:
        print("usage: normalize_probe_coords.py <probe.json>...")
        sys.exit(1)
    for f in sys.argv[1:]:
        src = Path(f)
        data = load(src)
        moves, total = normalize(data)
        out = src.with_name(src.stem + ".norm.json")
        dump(out, data)
        print("%s -> %s  (坐标修正 %d/%d)" % (src.name, out.name, moves, total))


if __name__ == "__main__":
    main()