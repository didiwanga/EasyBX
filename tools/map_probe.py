#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/map_probe.py — 北侠 MUD 地图数据采集探针（独立脚本，不依赖 xkxclient/PyQt）。

用法：
  python tools/map_probe.py --user <英文名> --passwd <密码>
  python tools/map_probe.py --user x --passwd y --steps 10 --out probe_dump.bin

行为：
  1) 复刻客户端登录：编码数字(GBK=1) -> 英文名 -> 密码 -> MXP 回车回退 -> 进游戏。
  2) 登录后发 GMCP Core.Hello 建立 GMCP 通道。
  3) 自动 look + DFS 本地探索（深度上限内漫游）：采集房间名/出口(GMCP.Move dir 与
     look 出口)/look 描述/ASCII 小地图行。
  4) 探索结束后依次探测 node / walk / where 输出。
  5) 全程原始字节落盘 <out>（GBK、含 ANSI/telnet），事件日志落盘 <out>.log（UTF-8）。
  6) 会话尽量短，避免 fullme 降级房间信息。

安全：只在起始区域附近做少量漫游（默认 8 步）；所有等待有超时，到点发 quit。
凭据只经命令行参数传入，绝不写进仓库。
"""

import argparse
import json
import os
import re
import select
import socket
import sys
import time
from collections import deque

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
GMCP_OPT = 0xC9
GA_OPT = 0xF9

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07")
_MXP_RE = re.compile(r"\x1b\[1z<[^>]*>")

_EXIT_LINE_RE = re.compile(r"这里明显的(?:方向|出口)[有是][：:]?\s*(.*)")
_ROOM_TITLE_RE = re.compile(r"^([^-]+?)\s*-\s*(?:★\s*)?$")
_MAP_CHARS = ("│", "｜", "┌", "┐", "├", "┤", "└", "┘", "＼", "／", "〓", "═", "─", "━")
_DESC_STARTS = ("这里是", "这是一个", "这是(")
# NPC 独立行：`名字 [称号] (英文id)`，整行即名字+括号 id（英文 id 可含空格）。
# 排除描述/物件行（"你可以看看(look)"、牌子等）。
_NPC_LINE_RE = re.compile(
    r"^(?P<name>[\u4e00-\u9fff]{2,}(?:[ 　][\u4e00-\u9fff]{2,})*?)\s*"
    r"\((?P<en>[A-Za-z][A-Za-z0-9_ ]*)\)\s*$")
_NPC_SKIP = ("看看", "牌子", "墙上", "这里", "〈", "〉")

_DIR_OPPOSITE = {
    "north": "south", "south": "north", "east": "west", "west": "east",
    "northwest": "southeast", "southeast": "northwest",
    "northeast": "southwest", "southwest": "northeast",
    "up": "down", "down": "up",
    "enter": "out", "out": "enter",
    "northup": "southdown", "southdown": "northup",
    "southup": "northdown", "northdown": "southup",
    "eastup": "westdown", "westdown": "eastup",
    "westup": "eastdown", "eastdown": "westup",
    "northeastup": "southwestdown", "southwestdown": "northeastup",
    "southwestup": "northeastdown", "northeastdown": "southwestup",
    "northwestup": "southeastdown", "southeastdown": "northwestup",
    "southeastup": "northwestdown", "northwestdown": "southeastup",
}
_DIRS = set(_DIR_OPPOSITE)

# 坐标增量（x 东为正，y 北为正，z 向上为正）。enter/out 视为原位。
_DIR_DELTA = {
    "north": (0, 1, 0), "south": (0, -1, 0),
    "east": (1, 0, 0), "west": (-1, 0, 0),
    "northeast": (1, 1, 0), "southeast": (1, -1, 0),
    "northwest": (-1, 1, 0), "southwest": (-1, -1, 0),
    "up": (0, 0, 1), "down": (0, 0, -1),
    "enter": (0, 0, 0), "out": (0, 0, 0),
    "northup": (0, 1, 1), "southdown": (0, -1, -1),
    "southup": (0, -1, 1), "northdown": (0, 1, -1),
    "eastup": (1, 0, 1), "westdown": (-1, 0, -1),
    "westup": (-1, 0, 1), "eastdown": (1, 0, -1),
    "northeastup": (1, 1, 1), "southwestdown": (-1, -1, -1),
    "southwestup": (-1, -1, 1), "northeastdown": (1, 1, -1),
    "northwestup": (-1, 1, 1), "southeastdown": (1, -1, -1),
    "southeastup": (1, -1, 1), "northwestdown": (-1, 1, -1),
}


class TelnetParser:
    """剥离 IAC，回 DO 0xC9，收集 SB GMCP payload 与 GA 标记，产出纯文本字节。

    使用持久缓冲：跨 TCP 分片的不完整 IAC/SB 序列保留到下一段。
    """

    def __init__(self):
        self.buf = bytearray()
        self.text = bytearray()
        self.gmcp: list[bytes] = []
        self.ga_seen = False

    def feed(self, data: bytes, reply_holder) -> None:
        self.buf += data
        self._parse(reply_holder)

    def _parse(self, reply_holder) -> None:
        n = len(self.buf)
        i = 0
        text_out = bytearray()
        while i < n:
            c = self.buf[i]
            if c == IAC:
                if i + 1 >= n:
                    break
                cmd = self.buf[i + 1]
                if cmd in (DO, DONT, WILL, WONT):
                    if i + 2 >= n:
                        break
                    opt = self.buf[i + 2]
                    if cmd == WILL and opt == GMCP_OPT:
                        reply_holder(bytes([IAC, DO, GMCP_OPT]))
                    i += 3
                elif cmd == SB:
                    j = i + 2
                    while j + 1 < n and not (self.buf[j] == IAC and self.buf[j + 1] == SE):
                        j += 1
                    if j + 1 >= n:
                        break
                    if self.buf[i + 2] == GMCP_OPT:
                        self.gmcp.append(bytes(self.buf[i + 3:j]))
                    i = j + 2
                elif cmd == SE:
                    i += 2
                elif cmd == GA_OPT:
                    self.ga_seen = True
                    i += 2
                elif cmd == IAC:
                    text_out.append(0xFF)
                    i += 2
                else:
                    i += 2
            else:
                j = i
                while j < n and self.buf[j] != IAC:
                    j += 1
                text_out += self.buf[i:j]
                i = j
        self.text += text_out
        if i >= n:
            self.buf = bytearray()
        else:
            del self.buf[:i]

    def take_gmcp(self) -> list[bytes]:
        out, self.gmcp = self.gmcp, []
        return out


def clean_line(raw: bytes) -> str:
    s = raw.decode("gbk", errors="replace")
    s = ANSI_RE.sub("", s)
    s = s.replace("\x01", "")
    return s.strip()


class MapProbe:
    def __init__(self, args):
        self.args = args
        self.host = args.host
        self.port = args.port
        self.user = args.user
        self.passwd = args.passwd
        self.sock = None
        self.parser = TelnetParser()
        self.tbuf = bytearray()
        self.dump = bytearray()
        self.logs: list[str] = []
        self.deadline = time.time() + args.max_time
        self.finished = False

        # 登录状态
        self.stage = "boot"
        self.enc_sent = False
        self.name_sent = False
        self.pw_sent = False
        self.pw_time = 0.0
        self.mxp_push = False
        self.kick_answered = False
        self.pending: list[tuple[float, str]] = []
        self.hold_advance = False
        self.hold_until = 0.0

        # 采集状态
        self.capture_look = False
        self.capture_since = 0.0
        self.look_buf = ""
        self.room = ""                # 当前房间实例 id
        self.room_name = ""           # 当前房间显示名（仅日志/调试）
        self.exits_now: list[str] = []
        self.gmcp_moves: list[dict] = []
        self.last_sent = ""
        self.room_records: dict[str, dict] = {}   # 实例id -> 结构化房间记录（累积样本）
        self.gmcp_other: set[str] = set()         # 出现过的其他 GMCP 模块

        # 探索（节点为房间实例：同名但出口集/位置不同 → 不同节点）
        self.nodes: dict[str, dict] = {}
        self.name_to_ids: dict[str, list[str]] = {}
        self._next_id = 0
        self.steps_done = 0
        self.moving = False
        self.move_pending_dir = ""
        self.move_timeout = 0.0
        self.simplified = False   # fullme 降级（简化回包）状态

        # 探测任务：(kind, arg, 采集时长ms)。kind=cmd 发命令采集；kind=walkto 内部寻路走到 arg 房间
        # 区域边界验证：泰安(钟鼓楼/县衙) vs 过渡(北门/山路/大驿道) vs 泰山(岱宗坊)
        self.probe_todo = [
            ("cmd", "node", 2500),
            ("walkto", "钟鼓楼", 40000),
            ("cmd", "walk -c", 8000),
            ("cmd", "walk taishan", 60000),
            ("cmd", "walk -c", 8000),
            ("cmd", "node", 2500),
            ("walkto", "大驿道", 40000),
            ("cmd", "walk -c", 8000),
            ("walkto", "北门", 40000),
            ("cmd", "walk -c", 8000),
            ("walkto", "县衙", 40000),
            ("cmd", "walk -c", 8000),
            ("cmd", "maphere", 4000),
        ]
        if getattr(self.args, "task", None):
            # 命令行 --task kind:arg:ms（可多次）覆盖默认任务表
            todo = []
            for t in self.args.task:
                parts = t.split(":")
                if len(parts) >= 3:
                    todo.append((parts[0], parts[1], int(parts[2])))
                elif len(parts) == 2:
                    todo.append((parts[0], "", int(parts[1])))
                else:
                    todo.append((t, "", 2500))
            self.probe_todo = todo
        self.probe_phase = False
        self.probe_start = 0.0
        self.probe_last_text = 0.0
        self.probe_deadline = 0.0
        self.probe_buf: list[str] = []
        self.walkto_target: str | None = None
        self.walkto_target_name = ""
        self.walkto_path: list[str] = []
        self.walkto_active = False

        # explore：BFS 遍历当前可达区域（走所有未走边），无前沿或超时结束
        self.explore_active = False
        self.explore_path: list[str] = []
        self._explore_dirs: list[str] = []

        # 动作型任务（ferry/gu）：发命令后等待一次 GMCP.Move（到达对岸/目的地），
        # 移动后延迟 5s 收尾采集；无到达则超时（20s）结束。
        self.action_move_wait = False
        self.action_move_end = 0.0
        self.action_deadline = 0.0
        self._ferry_topic = ""
        self._ferry_ids: list[str] = []
        self._ferry_going = False
        self._ferry_boarded = False
        self._ferry_disembarked = False

        self._last_activity = time.time()
        self._load_map_cache()

    def _load_map_cache(self) -> None:
        """启动时加载客户端地图缓存，使 walkto 可跨会话寻路到已知房间（马车行/渡口等）。

        缓存节点 coords 未知统一置 (0,0,0)，仅在 GMCP.Move 到位时按边一致性复用。
        """
        path = os.path.expandvars(r"%APPDATA%\XkxClient\config\map_cache.json")
        try:
            with open(path, "rb") as f:
                raw = f.read()
            data = json.loads(raw.decode("utf-8-sig"))
        except Exception as e:
            self.logs.append(f"[cache] 加载 map_cache 失败: {e}")
            return
        rooms = data.get("rooms") or {}
        edges = data.get("edges") or {}
        n = 0
        for name in rooms:
            nid = f"c{n}"
            n += 1
            exits = {x for x in (rooms[name].get("exits") or []) if x in _DIRS}
            coords = (rooms[name].get("coords") or [])[:3]
            known = bool(coords) and any(coords)
            self.nodes[nid] = {"name": name, "exits": exits, "walked": set(exits),
                               "neighbors": {}, "coords": list(coords or [0, 0, 0]),
                               "coords_known": known, "from_cache": True}
            self.name_to_ids.setdefault(name, []).append(nid)
            self.room_records[nid] = {"name": name, "gmcp_exits_samples": [],
                                      "look_exits": sorted(exits), "npc": [],
                                      "desc": [], "map": [], "raw": []}
        # 骨架边加载为虚拟邻居：walkto 可跨会话寻路到已知房间。
        # 缓存节点 walked=全 exits，explore 不会将其作为探索前沿，故无虚假路径风险。
        for a, eds in edges.items():
            ia = (self.name_to_ids.get(a) or [None])[0]
            if ia is None:
                continue
            for d, b in eds.items():
                ib = (self.name_to_ids.get(b) or [None])[0]
                if ib is None or ib == ia or d not in _DIRS:
                    continue
                na = self.nodes[ia]["neighbors"]
                if d not in na:
                    na[d] = ib
                op = _DIR_OPPOSITE.get(d)
                nb = self.nodes[ib]["neighbors"]
                if op and op not in nb:
                    nb[op] = ia
        self.logs.append(f"[cache] map_cache 已加载: {len(rooms)} rooms（含骨架边 {len(edges)} 条）")

    # ---- 输出 ----
    def log(self, s: str) -> None:
        self.logs.append(f"[{time.strftime('%H:%M:%S')}] {s}")

    def dump_append(self, data: bytes) -> None:
        self.dump += data
        with open(self.args.out, "ab") as f:
            f.write(data)

    # ---- 网络 ----
    def send_text(self, text: str) -> None:
        self.last_sent = text.strip()
        self.logs.append(f">> {text}")
        data = text.encode("gbk", errors="replace") + b"\r\n"
        self.dump_append(data)
        try:
            self.sock.sendall(data)
        except OSError as e:
            self.logs.append(f"!! send 失败: {e}")

    def send_raw(self, data: bytes) -> None:
        self.dump_append(data)
        try:
            self.sock.sendall(data)
        except OSError as e:
            self.logs.append(f"!! send_raw 失败: {e}")

    def schedule(self, text: str, after_ms: int = 500) -> None:
        self.pending.append((time.time() + after_ms / 1000.0, text))

    def poll_scheduled(self) -> None:
        now = time.time()
        due = [t for t in self.pending if t[0] <= now]
        self.pending = [t for t in self.pending if t[0] > now]
        for _, text in sorted(due, key=lambda t: t[0]):
            if text == "look":
                self.capture_look = True
                self.capture_since = time.time()
                self.look_buf = ""
            self.send_text(text)

    # ---- 登录 ----
    def start_gmcp_hello(self) -> None:
        payload = b'Core.Hello {"client":"map_probe","version":"0.1"}'
        self.send_raw(bytes([IAC, SB, GMCP_OPT]) + payload + bytes([IAC, SE]))

    def _kick_watchdog(self) -> None:
        if self.stage == "ready" or self.finished:
            return
        if self.pw_sent and self.pw_time and time.time() - self.pw_time > 60:
            self.log("!! 登录看门狗：密码已发 60s 未完成，强制进入游戏")
            self._enter_game()

    def on_text_line(self, line: str) -> None:
        if self.finished:
            return
        if self.stage != "ready":
            self._login_line(line)
            return
        self._game_line(line)

    def _login_line(self, line: str) -> None:
        if self.stage in ("boot", "name"):
            if not self.enc_sent and ("Input 1 for GBK" in line or "编码已改为" in line):
                self.enc_sent = True
                self.schedule("1", 200)
            if "您的英文名字" in line and not self.name_sent and self.user:
                self.name_sent = True
                self.stage = "name_sent"
                self.schedule(self.user, 400)
        elif self.stage == "name_sent":
            if not self.pw_sent and ("请输入密码" in line or "密码" in line):
                self.pw_sent = True
                self.pw_time = time.time()
                self.stage = "password_sent"
                self.schedule(self.passwd, 400)
        elif self.stage == "password_sent":
            if "不支持MXP" in line or "使用普通文本模式" in line:
                self._enter_game()
                return
            if "即将开始检测你的客户端" in line and not self.mxp_push:
                self.mxp_push = True
                self.schedule("", 200)
            if "推荐使用Mudlet" in line:
                self.schedule("", 300)
            if not self.kick_answered and ("y/n" in line or "杀掉" in line or "杀出去" in line):
                self.kick_answered = True
                self.schedule("y", 300)

    def _enter_game(self) -> None:
        self.stage = "ready"
        self.log("==== 登录完成，进入游戏 ====")
        self.start_gmcp_hello()
        # 对齐客户端：登录后开启 GMCP 各模块；等待角色入场完毕再 look
        self.schedule("tune gmcp buff on;tune gmcp move on;tune gmcp combat on;tune gmcp status on", 300)
        self.hold_advance = True
        self.hold_until = time.time() + 20.0
        self.schedule("look", 1800)

    def _hold_watchdog(self) -> None:
        if self.hold_advance and self.hold_until and time.time() > self.hold_until:
            self.log("!! 首 look 兜底：20s 未完成，强制开始探索")
            self.hold_advance = False
            self.capture_look = False
            self._maybe_next()

# ---- 游戏内 ----
    def _game_line(self, line: str) -> None:
        if "fullme" in line or "robot.php" in line:
            self.log(f"[fullme] {line[:120]}")
        if "最简化" in line or "简化" in line and "信息" in line:
            if not self.simplified:
                self.simplified = True
                self.log("!! 检测到 fullme 降级：服务器回包已简化，房间身份以 GMCP.Move 为准")
        if self.probe_phase:
            self.probe_buf.append(line)
            if self.probe_start:
                self.probe_last_text = time.time()
            # 移动艰难（河边/江边"一脚深一脚浅"）：重发当前方向并延长超时，
            # 直到 GMCP.Move 到达才推进（与客户端 Navigator 重试逻辑一致）
            if self.moving and self.move_pending_dir and "一脚深一脚浅" in line:
                self.move_timeout = time.time() + 8.0
                self.logs.append(f"[probe] 移动艰难，重发 {self.move_pending_dir}")
                self.send_text(self.move_pending_dir)
            # ferry 渡江进行中：检测船程文本，延长等待（渡江动画 20s+），避免误判重试
            if self.action_deadline and self.action_move_wait and not self._ferry_going and \
               any(k in line for k in ("船资", "扁舟", "艄公", "船夫", "江心", "渡船", "上船", "摇橹")):
                self._ferry_going = True
                self.action_deadline = time.time() + 180.0
                self.logs.append("[probe] 渡江进行中，延长等待至 180s")
            # 上船/下船状态机：ask 船夫 → enter 上船 → 到岸 → out 下船
            if self._ferry_going:
                if not self._ferry_boarded and \
                   any(k in line for k in ("踏脚板", "上来吧", "上船吧", "等你呢")):
                    self._ferry_boarded = True
                    self.action_deadline = time.time() + 120.0
                    self.logs.append("[probe] 上船信号，enter")
                    self.send_text("enter")
                elif self._ferry_boarded and not self._ferry_disembarked and \
                     any(k in line for k in ("到啦", "上岸吧", "赶下了", "下船吧", "请下船")):
                    self._ferry_disembarked = True
                    self.action_deadline = time.time() + 30.0
                    self.logs.append("[probe] 到岸信号，out")
                    self.send_text("out")
        if self.capture_look:
            # 频道/多字符提示不进 look 缓冲，避免 QQ 群刷屏污染
            if line.startswith("【") or line.startswith(">>"):
                pass
            elif line.startswith(">"):
                # 裸提示符：look 输出结束（出口行后的 NPC/物品区已收齐）
                self.capture_look = False
                buf, self.look_buf = self.look_buf, ""
                self._finish_look(buf)
            else:
                self.look_buf += line + "\n"

    def _finish_look(self, buf: str) -> None:
        lines = [l for l in buf.splitlines() if l.strip()]
        name, exits = "", []
        for l in lines:
            tm = _ROOM_TITLE_RE.match(l)
            if tm:
                name = tm.group(1).strip()
            em = _EXIT_LINE_RE.search(l)
            if em:
                for w in re.split(r"[,，、;；\s和]+", em.group(1) or ""):
                    w = w.strip().strip("。．").lower()
                    if w in _DIRS:
                        exits.append(w)
        if not name:
            name = self.room_name
        if self.room not in self.nodes:
            # look 先于任何 GMCP 到达（极少数情况）：按名字+出口解析身份
            self.room = self._resolve_room(name, set(), None, "")
            self.room_name = name
        rec = self.room_records[self.room]
        rec["look_exits"] = exits or rec["look_exits"]
        rec["raw"].append(buf)
        self.logs.append(f"[look] room={name!r} exits={exits}")
        for l in lines:
            s = l.strip()
            if s and "(" in s and ")" in s and not any(k in s for k in _NPC_SKIP):
                m = _NPC_LINE_RE.match(s)
                if m:
                    cn, en = m.group("name").strip(), m.group("en").strip()
                    if [cn, en] not in rec["npc"]:
                        rec["npc"].append([cn, en])
                        self.logs.append(f"[npc ] {cn}({en})")
            if l.startswith(_DESC_STARTS):
                if l not in rec["desc"]:
                    rec["desc"].append(l)
                self.logs.append(f"[desc] {l}")
            elif any(ch in l for ch in _MAP_CHARS) or l.count("-") >= 2:
                if l not in rec["map"]:
                    rec["map"].append(l)
                self.logs.append(f"[map ] {l}")
        if not self.moving and exits and not self.exits_now:
            # GMCP.Move 是出口主源；look 仅在 GMCP 没给出口时补充
            self.exits_now = exits
        self.hold_advance = False
        self._after_room()

    def on_gmcp(self, payload: bytes) -> None:
        try:
            text = payload.decode("gbk", errors="replace")
        except Exception:
            return
        mod, _, rest = text.partition(" ")
        mod = mod.strip()
        if not rest:
            self.logs.append(f"[gmcp] {mod}")
            return
        if mod == "Core.Hello":
            self.logs.append(f"[gmcp] Core.Hello {rest[:60]}")
            return
        try:
            data = json.loads(rest)
        except Exception:
            self.logs.append(f"[gmcp] {mod} (parse fail) {rest[:120]}")
            return
        if mod == "GMCP.Move":
            if isinstance(data, list):
                data = data[0] if data else {}
            self._on_move(data)
        elif mod in ("GMCP.Combat", "GMCP.Buff", "GMCP.Message"):
            self.logs.append(f"[gmcp] {mod}: {rest[:100]}")
        else:
            self.gmcp_other.add(mod)
            self.logs.append(f"[gmcp] {mod}: {rest[:140]}")

    def _on_move(self, d: dict) -> None:
        ok = str(d.get("result", "")).lower() == "true"
        name = str(d.get("short") or "")
        dirs = [x for x in (d.get("dir") or []) if x in _DIRS]
        self.logs.append(f"[gmcp.Move] ok={ok} room={name!r} dir={dirs}")
        self.gmcp_moves.append({"room": name, "ok": ok, "dir": dirs})
        if self.stage != "ready":
            # 登录期到达：只记身份/出口，不推进（服务器登录到达的 GMCP.Move）
            if name:
                nid = self._resolve_room(name, set(dirs), None, "")
                self.room = nid
                self.room_name = name
                self.exits_now = dirs or self.exits_now
                if dirs:
                    self.room_records[nid]["gmcp_exits_samples"].append(list(dirs))
            self.move_pending_dir = ""
            self.moving = False
            return
        if not ok:
            # 撞墙：标记方向已尝试，并删除坏边（缓存虚拟边常串边），防重寻路反复走同一坏边
            node = self.nodes[self.room]
            bad = self.move_pending_dir
            if bad:
                node["walked"] |= {bad}
                tgt = node.get("neighbors", {}).pop(bad, None)
                if tgt:
                    tn = self.nodes.get(tgt)
                    if tn:
                        tn.get("neighbors", {}).pop(_DIR_OPPOSITE.get(bad), None)
            self.logs.append(f"[move] 撞墙/失败 (dir was {bad!r})")
            self.move_pending_dir = ""
            self.moving = False
            self.capture_look = False
            if self.probe_phase:
                if self.walkto_active:
                    # walkto 途中撞墙：重新寻路或放弃
                    if not self._walkto(self.walkto_target_name):
                        self.logs.append("[probe] walkto 撞墙后无路，放弃")
                        self.walkto_active = False
                        self.walkto_path = []
                        self._end_probe()
                elif self.explore_active:
                    # explore 途中撞墙：该方向已标记，重新规划
                    self.logs.append("[probe] explore 撞墙，重规划")
                    self.explore_path = []
                    self._explore_step()
                return
            self._maybe_next()
            return
        if name:
            prev = self.room
            pend = self.move_pending_dir
            nid = self._register_room(name, dirs, prev, pend)
            self.room = nid
            self.room_name = name
            self.exits_now = dirs or self.exits_now
            if dirs:
                self.room_records[nid]["gmcp_exits_samples"].append(list(dirs))
            if self.probe_phase:
                # walk/walkto 行走中：只记录房间与坐标，不推进探索（避免与 walk 命令打架）
                self.moving = False
                self.capture_look = False
                if self.action_move_wait:
                    # ferry/gu 到达对岸/目的地：延迟 5s 收尾采集
                    self.action_move_wait = False
                    self.action_move_end = time.time() + 5.0
                    self.logs.append(f"[probe] {self.probe_cmd} 到达 {name!r} dir={dirs}")
                elif self.walkto_active:
                    if self.walkto_target_name and name == self.walkto_target_name:
                        # 到达目标名的房间（任意实例）即完成，避免同名多实例绕圈
                        self.logs.append(f"[probe] walkto 命中目标名 {name!r}")
                        self._finish_walkto()
                    else:
                        self._walkto_step()
                elif self.explore_active:
                    self._explore_step()
            else:
                self._on_move_ok()
        else:
            self.move_pending_dir = ""
            self.moving = False

    def _new_node(self, name: str, dirs: set[str], coords: tuple[int, int, int]) -> str:
        nid = f"n{self._next_id}"
        self._next_id += 1
        known = not (coords[0] == coords[1] == coords[2] == 0)
        self.nodes[nid] = {"name": name, "exits": set(dirs), "walked": set(),
                           "neighbors": {}, "coords": list(coords), "coords_known": known}
        self.name_to_ids.setdefault(name, []).append(nid)
        self.room_records[nid] = {
            "name": name, "gmcp_exits_samples": [], "look_exits": [],
            "npc": [], "desc": [], "map": [], "raw": [],
        }
        return nid

    def _resolve_room(self, name: str, dirs: set[str], prev: str | None, d: str,
                      expected: tuple[int, int, int] | None = None) -> str:
        """到达房间的身份辨识（同名多出口集实例靠出口集区分）。

        优先级：1) 边一致性（prev 沿 d 走过 → 必是同一房间）
                2) 名字 + 出口集一致（同名同出口即合并，坐标仅作新建房间初值）
                3) 新建节点
        """
        if prev and d:
            pn = self.nodes.get(prev) or {}
            hit = pn.get("neighbors", {}).get(d)
            if hit:
                return hit
        for nid in self.name_to_ids.get(name, []):
            nd = self.nodes[nid]
            if nd["exits"] == dirs:
                if expected and nd.get("coords_known") and tuple(nd.get("coords") or ()) != tuple(expected):
                    # 移动坐标已知且不符 → 判定为同名的另一处（如连续的城墙脚下），新建节点
                    self.logs.append(f"[id] 同名同出口坐标不符: {name!r} {nd.get('coords')} vs {list(expected)}")
                    continue
                if prev and d:
                    op = _DIR_OPPOSITE.get(d) or ""
                    if nd.get("neighbors", {}).get(op) == prev:
                        return nid
                return nid
        if prev is None:
            # 登录/初始到达：复用缓存同名节点，保证 walkto 可借助骨架边跨会话寻路
            for nid in self.name_to_ids.get(name, []):
                if self.nodes[nid].get("from_cache"):
                    return nid
        return self._new_node(name, dirs, expected or (0, 0, 0))

    def _register_room(self, name: str, dirs: list[str], prev: str, d: str) -> str:
        expected: tuple[int, int, int] | None = None
        if prev and d:
            base = self.nodes.get(prev, {}).get("coords")
            if base:
                delta = _DIR_DELTA.get(d, (0, 0, 0))
                expected = (base[0] + delta[0], base[1] + delta[1], base[2] + delta[2])
        nid = self._resolve_room(name, set(dirs), prev, d, expected)
        node = self.nodes[nid]
        node["exits"] |= set(dirs)
        if node.get("coords") is None:
            node["coords"] = list(expected) if expected else [0, 0, 0]
            node["coords_known"] = bool(expected)
        elif expected and node.get("coords_known") and tuple(node["coords"]) != tuple(expected):
            self.logs.append(f"[coord] 坐标冲突 {name!r} {node['coords']} vs {list(expected)}")
        if d and prev:
            op = _DIR_OPPOSITE.get(d) or ""
            # 从 prev 沿 d 到达 name：prev 的 d 出口已走，name 的返回方向 op 已走
            node["walked"] |= {op}
            node["neighbors"][op] = prev
            pn = self.nodes[prev]
            pn["walked"] |= {d}
            pn["neighbors"][d] = nid
        self.move_pending_dir = ""
        return nid

    def _on_move_ok(self) -> None:
        self.steps_done += 1
        self.moving = False
        self.capture_look = True
        self.capture_since = time.time()
        self.look_buf = ""
        self.logs.append(f"[walk] steps={self.steps_done} room={self.room_name!r} simplified={self.simplified}")
        if not self.hold_advance:
            self._maybe_next()

    # ---- 探索决策（图驱动 BFS 前沿）----
    def _after_room(self) -> None:
        if self.finished or self.moving:
            return
        if self.steps_done < self.args.steps:
            self._maybe_next()
        else:
            self._start_probe_phase()

    def _maybe_next(self) -> None:
        if self.moving or self.finished:
            return
        if self.steps_done >= self.args.steps:
            self._start_probe_phase()
            return
        node = self.nodes.setdefault(self.room, {"name": self.room_name, "exits": set(), "walked": set(), "neighbors": {}, "coords": [0, 0, 0]})
        node["exits"] |= set(self.exits_now)
        unvisited = [d for d in self.exits_now if d not in node["walked"]]
        if unvisited:
            self._go(unvisited[0])
            return
        # 当前房间所有已知边都走过：找图里仍有未走边的房间，沿已建边 BFS 前往
        target = self._find_frontier()
        if target:
            path = self._route_to(target)
            if path:
                self._go(path[0])
                return
        self.log("[walk] 无可探索边，停止探索")
        self._start_probe_phase()

    def _find_frontier(self) -> str | None:
        """BFS 返回第一个存在未走边的房间（基于已建立的邻居边）。"""
        start = self.room
        seen = {start}
        q = deque([start])
        while q:
            cur = q.popleft()
            node = self.nodes.get(cur) or {}
            if any(d not in node.get("walked", set()) for d in node.get("exits", set())):
                return cur
            for nxt in (node.get("neighbors") or {}).values():
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        return None

    def _route_to(self, target: str) -> list[str]:
        """BFS 方向路径到 target（只用已建立的邻居边）。"""
        if target == self.room:
            return []
        start = self.room
        prev = {start: None}
        q = deque([start])
        while q:
            cur = q.popleft()
            if cur == target:
                path = []
                n = cur
                while prev[n] is not None:
                    p, d = prev[n]
                    path.append(d)
                    n = p
                return list(reversed(path))
            for d, nxt in (self.nodes.get(cur, {}).get("neighbors") or {}).items():
                if nxt not in prev:
                    prev[nxt] = (cur, d)
                    q.append(nxt)
        return None

    def _go(self, d: str) -> None:
        if not d:
            self._maybe_next()
            return
        self.moving = True
        self.move_pending_dir = d
        self.move_timeout = time.time() + 8.0
        self.capture_look = True
        self.capture_since = time.time()
        self.look_buf = ""
        self.logs.append(f"[walk] go {d}")
        self.send_text(d)

    def _move_watchdog(self) -> None:
        if self.stage != "ready":
            return
        if self.moving and self.move_timeout and time.time() > self.move_timeout:
            stalled = self.move_pending_dir
            self.logs.append(f"!! 移动看门狗：{stalled!r} 8s 无 GMCP.Move，强制跳过")
            self.move_pending_dir = ""
            self.move_timeout = 0.0
            self.moving = False
            self.capture_look = False
            if self.probe_phase and self.walkto_active:
                # walkto 途中卡死：删除该方向（坏边/服务器无响应），重寻路
                node = self.nodes.get(self.room)
                if node is not None and stalled:
                    tgt = node.get("neighbors", {}).pop(stalled, None)
                    if tgt:
                        tn = self.nodes.get(tgt)
                        if tn:
                            tn.get("neighbors", {}).pop(_DIR_OPPOSITE.get(stalled), None)
                self.logs.append("[probe] walkto 移动看门狗，重寻路")
                if not self._walkto(self.walkto_target_name):
                    self.logs.append("[probe] walkto 移动看门狗后无路，放弃")
                    self.walkto_active = False
                    self.walkto_path = []
                    self._end_probe()
                return
            if self.probe_phase and self.explore_active:
                # explore 途中卡死：标记该方向无效并重规划
                node = self.nodes.get(self.room)
                if node is not None and stalled:
                    node["walked"] |= {stalled}
                self.explore_path = []
                self._explore_step()
                return
            self._maybe_next()

    def _look_watchdog(self) -> None:
        if self.stage != "ready":
            return
        if self.capture_look and self.capture_since and time.time() - self.capture_since > 8.0:
            self.logs.append("!! look 捕获 8s 未完成，强制收尾")
            self.capture_look = False
            self.capture_since = 0.0
            buf, self.look_buf = self.look_buf, ""
            self._finish_look(buf)

    # ---- 探测阶段 ----
    def _start_probe_phase(self) -> None:
        if self.probe_phase or self.finished:
            return
        self.probe_phase = True
        self.capture_look = False
        self.log("==== 开始探测命令 node/walk/where ====")
        self._run_next_probe()

    def _run_next_probe(self) -> None:
        if not self.probe_todo:
            self.log("==== 采集完成 ====")
            self._quit()
            return
        kind, arg, collect_ms = self.probe_todo.pop(0)
        self.probe_buf = []
        self.probe_start = time.time()
        self.probe_last_text = time.time()
        self.probe_deadline = time.time() + collect_ms / 1000.0
        self.explore_active = False
        self.explore_path = []
        if kind == "cmd":
            self.probe_cmd = arg
            self.logs.append(f"[probe] >> {arg} (collect {collect_ms}ms)")
            if arg == "look":
                self.capture_look = True
                self.capture_since = time.time()
                self.look_buf = ""
                self.send_text(arg)
            elif arg in _DIRS:
                # 方向行走：走 _go 设置 move_pending_dir，让 GMCP.Move 到达时
                # 记录边（否则 cmd 直接 send_text 不建边，探针邻居全空）。
                self._go(arg)
            else:
                self.send_text(arg)
        elif kind == "walkto":
            self.probe_cmd = f"walkto {arg}"
            self.logs.append(f"[probe] walkto {arg} (上限 {collect_ms}ms)")
            if not self._walkto(arg):
                self.logs.append(f"[probe] walkto 失败：图中无路径到 {arg!r}")
                self._end_probe()
        elif kind == "ferry":
            self.probe_cmd = f"ferry:{arg}"
            self.logs.append(f"[probe] ferry {arg} (渡河/渡江)")
            self._run_ferry(arg)
        elif kind == "gu":
            self.probe_cmd = "gu"
            self.logs.append("[probe] gu (雇马车)")
            self._run_gu()
        elif kind == "pause":
            self.probe_cmd = f"pause:{arg}"
            self.logs.append(f"[probe] pause {collect_ms}ms (仅观察, 不发命令)")
            self._pause_task(collect_ms)
        elif kind == "explore":
            self.probe_cmd = "explore"
            self.logs.append(f"[probe] explore (BFS 遍历, 上限 {collect_ms}ms)")
            # 缓存节点出口置为未走，允许沿真实移动重新探索（缓存边可能缺失/孤立）
            for nid, nd in self.nodes.items():
                if nd.get("from_cache"):
                    nd["walked"] = set()
            self.explore_active = True
            self._explore_visited = {self.room}
            self._explore_no_new = 0
            self._explore_moves = 0
            self._explore_dirs = []
            self._explore_step()

    def _pause_task(self, ms: int) -> None:
        """不发命令的纯观察任务：等 ms 毫秒后结束（用于观察渡河/雇车结果）。"""
        self.action_deadline = time.time() + ms / 1000.0
        self.action_move_wait = False
        self.action_move_end = 0.0

    def _run_ferry(self, topic: str) -> None:
        """找当前房间船夫 NPC，ask about <topic>（huanghe/jiang）渡河。
        英文 id 带空格时（如 `Wu yi`）连写小写再试，直到 GMCP.Move 到达。"""
        rec = self.room_records.get(self.room, {})
        npcs = rec.get("npc", []) or []
        cand = [n for n in npcs if any(k in n[0] for k in ("船", "渡", "艄", "舟"))]
        if not cand:
            self.logs.append(f"[probe] 当前房间 {self.room_name!r} 无船夫 NPC，跳过")
            self._end_probe()
            return
        self._ferry_topic = topic
        self._ferry_ids = self._id_variants(cand[0][1])
        self.logs.append(f"[probe] 船夫 {cand[0][0]}({cand[0][1]}) id 候选: {self._ferry_ids}")
        self._ask_ferry()

    def _id_variants(self, en: str) -> list[str]:
        out = []
        raw = (en or "").strip()
        if raw:
            out.append(raw)
        compact = "".join(raw.split()).lower()
        if compact and compact not in out:
            out.append(compact)
        return out

    def _ask_ferry(self) -> None:
        if not self._ferry_ids:
            self.logs.append("[probe] 船夫 id 均无到达，放弃")
            self.action_deadline = 0.0
            self.action_move_end = 0.0
            self._end_probe()
            return
        en = self._ferry_ids.pop(0)
        topic = self._ferry_topic
        self._ferry_going = False
        self._ferry_boarded = False
        self._ferry_disembarked = False
        self.action_move_wait = True
        self.action_move_end = 0.0
        self.action_deadline = time.time() + 20.0
        self.logs.append(f"[probe] 渡{topic}: ask {en} about {topic}")
        self.send_text(f"ask {en} about {topic}")

    def _run_gu(self) -> None:
        """找当前房间车夫 NPC，gu 雇马车。"""
        rec = self.room_records.get(self.room, {})
        npcs = rec.get("npc", []) or []
        cand = [n for n in npcs if ("车夫" in n[0] or "马车" in n[0])]
        if not cand:
            self.logs.append(f"[probe] 当前房间 {self.room_name!r} 无车夫 NPC，跳过")
            self._end_probe()
            return
        self.action_move_wait = True
        self.action_move_end = 0.0
        self.action_deadline = time.time() + 20.0
        self.logs.append("[probe] 雇马车: gu")
        self.send_text("gu")

    def _walkto(self, target_name: str) -> bool:
        best: tuple[str, list[str]] | None = None
        for tid in self.name_to_ids.get(target_name, []):
            path = self._route_to(tid)
            if path and (best is None or len(path) < len(best[1])):
                best = (tid, path)
        if not best:
            return False
        self.walkto_target, self.walkto_path = best
        self.walkto_target_name = target_name
        self.walkto_active = True
        self._walkto_step()
        return True

    def _walkto_step(self) -> None:
        if not self.walkto_path:
            self._finish_walkto()
            return
        d = self.walkto_path.pop(0)
        self.move_pending_dir = d
        self.move_timeout = time.time() + 8.0
        self.moving = True
        self.logs.append(f"[probe] walkto 走 {d}")
        self.send_text(d)

    def _finish_walkto(self) -> None:
        self.walkto_active = False
        self.walkto_path = []
        self.moving = False
        self.move_pending_dir = ""
        self.logs.append(f"[probe] walkto 到达 {self.room_name!r}")
        self._end_probe()

    def _explore_step(self) -> None:
        """explore 走一步：当前房间有未走边先走；否则沿已建边 BFS 到最近前沿房间。
        到达即由 _on_move 的 probe_phase 分支再次调用，直到无可探索边或超时。"""
        if not self.explore_active:
            return
        # 连续多次回到已知节点说明在环路空转，停止以防死循环
        if self.room not in self._explore_visited:
            self._explore_visited.add(self.room)
            self._explore_no_new = 0
        else:
            self._explore_no_new += 1
            if self._explore_no_new >= 20:
                self.logs.append("[probe] explore 连续 20 步回到已知节点，停止")
                self.explore_active = False
                self._end_probe()
                return
        node = self.nodes.get(self.room) or {}
        if not self.explore_path:
            un = [d for d in node.get("exits", set()) if d not in node.get("walked", set())]
            if un:
                d = sorted(un)[0]
                self.nodes[self.room]["walked"].add(d)
                self._go_explore(d)
                return
            target = self._find_frontier()
            if target is None:
                self.logs.append("[probe] explore 完成：无可探索边")
                self.explore_active = False
                self._end_probe()
                return
            path = self._route_to(target)
            if not path:
                self.logs.append("[probe] explore 无法到达前沿，结束")
                self.explore_active = False
                self._end_probe()
                return
            self.explore_path = path[1:]
            self._go_explore(path[0])
            return
        self._go_explore(self.explore_path.pop(0))

    def _go_explore(self, d: str) -> None:
        self._explore_moves += 1
        if self._explore_moves > 400:
            self.logs.append("[probe] explore 移动 400 步上限，停止")
            self.explore_active = False
            self._end_probe()
            return
        # 方向循环检测：最近 8 步与上 8 步完全一致 -> 死循环空转，停止
        self._explore_dirs.append(d)
        if len(self._explore_dirs) >= 16 and self._explore_dirs[-8:] == self._explore_dirs[-16:-8]:
            self.logs.append("[probe] explore 方向循环 %s，停止" % ",".join(self._explore_dirs[-8:]))
            self.explore_active = False
            self._end_probe()
            return
        self.move_pending_dir = d
        self.move_timeout = time.time() + 8.0
        self.moving = True
        self.logs.append(f"[probe] explore 走 {d}")
        self.send_text(d)

    def _end_probe(self) -> None:
        self.probe_start = 0.0
        out = "\n".join(self.probe_buf)
        self.probe_buf = []
        for ln in (self.probe_cmd, out):
            self.logs.append(f"[probe-out] {ln}")
        self.logs.append(f"[probe] {self.probe_cmd} 输出 {len(out)} 字符")
        self._run_next_probe()

    def _process_pending_probe(self) -> None:
        if not self.probe_start:
            return
        now = time.time()
        if self.action_deadline:
            # 动作型任务（ferry/gu/pause）：等到达后收尾 或 超时结束
            if self.action_move_end and now >= self.action_move_end:
                self.action_deadline = 0.0
                self.action_move_end = 0.0
                self._end_probe()
                return
            if now > self.action_deadline:
                if getattr(self, "_ferry_ids", None) and not self._ferry_going:
                    # 本次 id 无到达且未在渡江：试下一个候选（连写 id）
                    self.logs.append("[probe] ask 无到达，试下一个 id")
                    self._ask_ferry()
                    return
                self.action_deadline = 0.0
                self.action_move_end = 0.0
                self.logs.append("[probe] 动作无到达，超时结束")
                self._end_probe()
                return
            return
        if self.walkto_active:
            if now > self.probe_deadline:
                self.logs.append("[probe] walkto 超时，放弃")
                self.walkto_active = False
                self.walkto_path = []
                self.moving = False
                self._end_probe()
            return
        # explore 由 _explore_step + 8s 移动看门狗驱动（移动艰难需长时间等待），
        # 不受"6s 无文本结束"规则影响，仅受 probe_deadline 上限约束
        if now > self.probe_deadline or (not self.explore_active and self.probe_last_text and now - self.probe_last_text > 6.0):
            self._end_probe()

    # ---- 结束 ----
    def _quit(self) -> None:
        if self.finished:
            return
        self.finished = True
        try:
            self.send_text("quit")
        except Exception:
            pass
        self.log("==== 退出 ====")

    def _write_log(self) -> None:
        with open(self.args.out + ".log", "w", encoding="utf-8") as f:
            f.write("\n".join(self.logs))

    def _write_rooms_json(self) -> None:
        path = self.args.out + ".rooms.json"
        nodes_out = []
        for nid in sorted(self.nodes, key=lambda x: int(x[1:])):
            nd = self.nodes[nid]
            nodes_out.append({
                "id": nid,
                "name": nd["name"],
                "coords": nd.get("coords"),
                "exits": sorted(nd["exits"]),
                "neighbors": {
                    d: {"to": t, "name": self.nodes[t].get("name", t)}
                    for d, t in sorted(nd["neighbors"].items())
                },
            })
        # 同名分组：同名出现多个实例 → 疑似跨区/多处同名
        same_name = {name: ids for name, ids in sorted(self.name_to_ids.items()) if len(ids) > 1}
        payload = {
            "host": self.host, "port": self.port, "user": self.user,
            "nodes": nodes_out,
            "same_name_groups": same_name,
            "room_records": self.room_records,
            "gmcp_move_count": len(self.gmcp_moves),
            "gmcp_other_modules": sorted(self.gmcp_other),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        self.logs.append(f"房间结构化记录 -> {path}")
        self.logs.append(f"节点数={len(self.nodes)} 同名分组: {len(same_name)} 组")

    def _summary(self) -> None:
        n_rooms = len(self.nodes)
        n_edges = sum(len(v["neighbors"]) for v in self.nodes.values())
        coords = sum(1 for v in self.nodes.values() if v.get("coords"))
        print(f"[summary] 节点数={n_rooms} 边数={n_edges} 有坐标={coords} GMCP.Move={len(self.gmcp_moves)}")
        print(f"[summary] 其他 GMCP 模块: {sorted(self.gmcp_other)}")
        print(f"[summary] 原始字节={len(self.dump)} -> {self.args.out}")
        print(f"[summary] 事件日志 -> {self.args.out}.log | 房间记录 -> {self.args.out}.rooms.json")

    # ---- 主循环 ----
    def run(self) -> None:
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=10)
        except OSError as e:
            print(f"连接失败: {e}")
            sys.exit(1)
        self.logs.append(f"已连接 {self.host}:{self.port}")
        self.send_raw(bytes([IAC, DO, GMCP_OPT]))
        self.enc_sent = True
        self.schedule("1", 450)
        while not self.finished and time.time() < self.deadline:
            self.poll_scheduled()
            self._kick_watchdog()
            self._process_pending_probe()
            self._move_watchdog()
            self._look_watchdog()
            self._hold_watchdog()
            try:
                r, _, _ = select.select([self.sock], [], [], 0.1)
            except OSError:
                break
            if r:
                try:
                    data = self.sock.recv(65536)
                except OSError:
                    break
                if not data:
                    self.log("!! 连接关闭")
                    break
                self._last_activity = time.time()
                self.dump_append(data)
                self.parser.feed(data, self.send_raw)
                self._drain_lines()
                for p in self.parser.take_gmcp():
                    self.on_gmcp(p)
        if not self.finished:
            self.log("!! 超时退出")
            self.finished = True
            try:
                self.send_text("quit")
            except Exception:
                pass
        self._write_log()
        self._write_rooms_json()
        self._summary()
        try:
            self.sock.close()
        except Exception:
            pass

    def _drain_lines(self) -> None:
        text = bytes(self.parser.text)
        self.parser.text = bytearray()
        self.tbuf += text
        while b"\n" in self.tbuf:
            raw, self.tbuf = bytes(self.tbuf).split(b"\n", 1)
            raw = raw.rstrip(b"\r")
            self._dispatch_line(raw)
        if self.parser.ga_seen:
            self.parser.ga_seen = False
            if self.tbuf:
                raw = bytes(self.tbuf)
                self.tbuf = bytearray()
                self._dispatch_line(raw)

    def _dispatch_line(self, raw: bytes) -> None:
        for m in _MXP_RE.finditer(raw.decode("gbk", errors="replace")):
            self.logs.append(f"[mxp] {m.group(0)[:80]}")
        line = clean_line(raw)
        self.on_text_line(line)


def main() -> None:
    ap = argparse.ArgumentParser(description="北侠 MUD 地图数据采集探针")
    ap.add_argument("--user", required=True)
    ap.add_argument("--passwd", required=True)
    ap.add_argument("--host", default="mud.pkuxkx.net")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--steps", type=int, default=25, help="探索步数上限")
    ap.add_argument("--out", default="probe_dump.bin", help="原始字节输出文件")
    ap.add_argument("--max-time", type=int, default=240, help="整个会话超时秒数")
    ap.add_argument("--probe-gap-ms", type=int, default=2500, help="probe 命令间隔")
    ap.add_argument("--task", action="append", default=None,
                    help="probe 任务 kind:arg:ms（可多次；kind=cmd/walkto/ferry/gu/pause），覆盖默认任务表")
    args = ap.parse_args()
    MapProbe(args).run()


if __name__ == "__main__":
    main()
