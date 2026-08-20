from __future__ import annotations

import re

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


def _npc_names(node: dict) -> list[str]:
    """取房间 node 的 NPC 中文名列表（兼容 str 旧格式 / {"name","id"} 新格式）。"""
    out = []
    for v in (node.get("npc") or []):
        if isinstance(v, dict):
            nm = v.get("name") or ""
        else:
            nm = str(v)
        if nm and nm not in out:
            out.append(nm)
    return out


class FlowLayout(QLayout):
    """流式布局：按可用宽度自动换行（Qt 官方 QFlowLayout 精简实现）。

    用于出口按钮区，避免出口过多时把 dock 拉得很宽；宽度变化时按钮自动折行，
    高度通过 hasHeightForWidth/heightForWidth 反向反馈给父布局。
    """

    def __init__(self, parent=None, hspacing: int = 4, vspacing: int = 4,
                 margin: int = 0) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h = hspacing
        self._v = vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.OrientationFlag:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        effective = QRect(rect.x() + m.left(), rect.y() + m.top(),
                          rect.width() - m.left() - m.right(),
                          rect.height() - m.top() - m.bottom())
        x, y = effective.x(), effective.y()
        line_height = 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y = y + line_height + self._v
                line_height = 0
            if apply:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h
            line_height = max(line_height, hint.height())
        return y + line_height + m.bottom() - rect.y()


class NavDock(QWidget):
    """导航 dock：实时显示当前 node + walk，并提供服务器 node 路径列表。

    实时数据源（随移动立即替换、不累积）：
    - state.room / GMCP.Move / map.pushed / look.parsed → 当前房间名/出口/详情
    - nav.*             → walk 进行中/已到达/卡住/停止 状态回显
    顶部出口按钮＝当前房间的可走方向（点=走一步）；底部目的地列表＝服务器
     `node` 命令返回的玩家定义路径（名称+目的地，双击=node walk <名称>）。
    node/walk 列表改为手动获取（点「获取」按钮）；`node` 表格行由 session
    拦截不上主输出。
    """

    def __init__(self, session=None, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self._bus = None
        self._account = getattr(session, "account_id", None) if session else None
        self._subs: list[tuple] = []

        # ---- 当前 node（实时）----
        self.room_label = QLabel("当前位置: -")
        self.room_label.setWordWrap(True)
        self.cat_label = QLabel("")
        self.npc_label = QLabel("")
        self.npc_label.setWordWrap(True)
        self.desc_label = QLabel("")
        self.desc_label.setWordWrap(True)
        self.desc_label.setMaximumHeight(90)
        self.desc_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._capturing = False
        self._seen_header = False
        self._after_node = None
        self._walk_capturing = False
        self._walk_seen_header = False
        self._cap_timer = QTimer(self)
        self._cap_timer.setSingleShot(True)
        self._cap_timer.setInterval(5000)
        self._cap_timer.timeout.connect(self._capture_timeout)

        self.exits_box = QWidget()
        self._exits_box_lay = FlowLayout(self.exits_box, hspacing=4, vspacing=4)
        self._exits_box_lay.setContentsMargins(0, 0, 0, 0)
        self._exit_btns: dict[str, QPushButton] = {}

        self.node_frame = QFrame()
        self.node_frame.setObjectName("navNodeFrame")
        self.node_frame.setFrameShape(QFrame.Shape.StyledPanel)
        nlay = QVBoxLayout(self.node_frame)
        nlay.setContentsMargins(6, 4, 6, 4)
        nlay.setSpacing(2)
        nlay.addWidget(self.room_label)
        nlay.addWidget(self.cat_label)
        nlay.addWidget(self.exits_box)
        nlay.addWidget(self.npc_label)
        nlay.addWidget(self.desc_label)
        nlay.addStretch(1)

        # ---- 目的地（服务器 node 命令列表，点击=node walk <名称>）----
        self.dest_ed = QLineEdit()
        self.dest_ed.setPlaceholderText("node 名称，回车=node walk <名称>")
        self.go_btn = QPushButton("获取")
        self.go_btn.clicked.connect(self._fetch_node_list)
        self.dest_ed.returnPressed.connect(self._send_node_walk)

        self.dest_list = QTreeWidget()
        self.dest_list.setColumnCount(2)
        self.dest_list.setHeaderLabels(["名称", "目的地"])
        self.dest_list.setRootIsDecorated(False)
        self.dest_list.setAlternatingRowColors(True)
        header = self.dest_list.header()
        if isinstance(header, QHeaderView):
            header.setStretchLastSection(True)
        self.dest_list.itemDoubleClicked.connect(lambda item, _c: self._send_node_walk(item))
        self.stop_btn = QPushButton("■ 停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)

        dest_top = QHBoxLayout()
        dest_top.addWidget(self.dest_ed, 1)
        dest_top.addWidget(self.go_btn)
        dest_btns = QHBoxLayout()
        dest_btns.addWidget(self.stop_btn)
        dest_btns.addStretch(1)

        dest_frame = QFrame()
        dest_frame.setObjectName("navDestFrame")
        dest_frame.setFrameShape(QFrame.Shape.StyledPanel)
        dlay = QVBoxLayout(dest_frame)
        dlay.setContentsMargins(6, 4, 6, 4)
        dlay.addWidget(QLabel("目的地（node 命令返回的玩家路径，点击=walk 过去）"))
        dlay.addLayout(dest_top)
        dlay.addWidget(self.dest_list, 1)
        dlay.addLayout(dest_btns)

        # ---- walk 内建路径表（服务器 walk 命令返回，双击=walk <拼音>）----
        self.walk_ed = QLineEdit()
        self.walk_ed.setPlaceholderText("walk 拼音名，回车=walk <拼音名>")
        self.walk_go_btn = QPushButton("获取")
        self.walk_go_btn.clicked.connect(self._fetch_walk_list)
        self.walk_ed.returnPressed.connect(self._send_walk_cmd)

        self.walk_list = QTreeWidget()
        self.walk_list.setColumnCount(3)
        self.walk_list.setHeaderLabels(["目的地", "拼音", "步数"])
        self.walk_list.setRootIsDecorated(False)
        self.walk_list.setAlternatingRowColors(True)
        walk_header = self.walk_list.header()
        if isinstance(walk_header, QHeaderView):
            walk_header.setStretchLastSection(True)
        self.walk_list.itemDoubleClicked.connect(lambda item, _c: self._send_walk_cmd(item))

        walk_top = QHBoxLayout()
        walk_top.addWidget(self.walk_ed, 1)
        walk_top.addWidget(self.walk_go_btn)

        walk_frame = QFrame()
        walk_frame.setObjectName("navDestFrame")
        walk_frame.setFrameShape(QFrame.Shape.StyledPanel)
        wlay = QVBoxLayout(walk_frame)
        wlay.setContentsMargins(6, 4, 6, 4)
        wlay.addWidget(QLabel("内建路径（walk 命令返回，点击=walk 过去）"))
        wlay.addLayout(walk_top)
        wlay.addWidget(self.walk_list, 1)

        # ---- walk 状态 ----
        self.status = QLabel("待命中")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(6)
        lay.addWidget(self.node_frame)
        lay.addWidget(dest_frame, 1)
        lay.addWidget(walk_frame, 1)
        lay.addWidget(self.status)
        lay.addStretch(0)

    # ---- 装配 ----
    def bind(self, session) -> None:
        self._unsub()
        self.session = session
        self._account = getattr(session, "account_id", None)
        bus = getattr(getattr(session, "app", None), "bus", None)
        self._bus = bus
        if bus is not None:
            for event in ("state.room", "GMCP.Move", "map.pushed", "look.parsed"):
                sub = bus.subscribe(event, self._on_event)
                self._subs.append((event, sub))
            sub = bus.subscribe("net.text_display", self._on_text)
            self._subs.append(("net.text_display", sub))
        self._sync_node(payload={
            "name": getattr(session, "room_name", "") or "",
            "exits": list(getattr(session, "exits", []) or []),
        })
        self._capturing = False
        # node/walk 列表改为手动获取：绑定账号、移动换房都不再自动刷新，
        # 需要时由用户点击「获取」按钮拉取，避免静默命令打断操作与产生噪声。

    def _unsub(self) -> None:
        if self._bus is not None:
            for event, sub in self._subs:
                try:
                    self._bus.unsubscribe(event, sub)
                except Exception:
                    pass
        self._subs = []

    # ---- 事件（实时替换，不累积）----
    def _on_event(self, payload: dict) -> None:
        acc = payload.get("account")
        if acc is not None and self._account is not None and acc != self._account:
            return
        ev = payload.get("event", "")
        if ev == "state.room":
            self._sync_node(payload)
        elif ev == "GMCP.Move":
            d = payload.get("data")
            if not isinstance(d, dict):
                d = {}
            ok = d.get("result")
            if not isinstance(ok, bool):
                ok = str(ok or "").lower() in ("true", "1",)
            if ok:
                self._sync_node({
                    "name": str(d.get("short") or ""),
                    "exits": list(d.get("dir") or []),
                })
        elif ev == "look.parsed":
            self._sync_look(payload.get("result"))
        elif ev == "map.pushed":
            self._refresh_from_cache()
        else:
            return

    def _sync_node(self, payload: dict) -> None:
        name = payload.get("name") or ""
        exits = list(payload.get("exits") or [])
        if not name and self.session is not None:
            name = getattr(self.session, "room_name", "") or ""
        if not exits and self.session is not None:
            exits = list(getattr(self.session, "exits", []) or [])
        cache = getattr(self.session, "map_cache", None) if self.session else None
        if not name and cache is not None:
            name = getattr(cache, "current", "") or ""
        # 无有效载荷（空 map.pushed 等）时仅保持现状，不清空
        if not name:
            return
        self.room_label.setText(f"当前位置: {name or '-'}")
        cat, npc, desc = "", [], []
        if cache is not None:
            node = cache.rooms.get(name) or {}
            cat = node.get("category") or ""
            npc = _npc_names(node)
            desc = list(node.get("desc") or [])
        self.cat_label.setText(f"类别: {cat}" if cat else "")
        self.npc_label.setText("NPC: " + "、".join(npc) if npc else "")
        self.desc_label.setText("\n".join(desc) if desc else "")
        self._set_exits(exits)

    def _refresh_from_cache(self) -> None:
        """从 MapCache 当前房间/rooms 重读 node 展示（map.pushed 后调用）。"""
        cache = getattr(self.session, "map_cache", None) if self.session else None
        if cache is None:
            return
        name = getattr(cache, "current", "")
        if not name:
            return
        node = cache.rooms.get(name) or {}
        self.room_label.setText(f"当前位置: {name}")
        cat = node.get("category") or ""
        npc = _npc_names(node)
        desc = list(node.get("desc") or [])
        self.cat_label.setText(f"类别: {cat}" if cat else "")
        self.npc_label.setText("NPC: " + "、".join(npc) if npc else "")
        self.desc_label.setText("\n".join(desc) if desc else "")
        self._set_exits(list(node.get("exits") or []))

    def _sync_look(self, result) -> None:
        if result is None:
            return
        room = getattr(result, "room", None)
        if room is None:
            return
        name = getattr(room, "name", "")
        if name:
            self.room_label.setText(f"当前位置: {name}")
        cat = getattr(room, "category", "") or ""
        self.cat_label.setText(f"类别: {cat}" if cat else "")
        desc = list(getattr(room, "desc", []) or [])
        self.desc_label.setText("\n".join(desc) if desc else "")
        exits = list(getattr(room, "exits", []) or [])
        if exits:
            self._set_exits(exits)
        entities = list(getattr(result, "entities", []) or [])
        names = [getattr(e, "name", "") for e in entities if getattr(e, "name", "")]
        self.npc_label.setText("NPC: " + "、".join(names) if names else "")

    def _set_exits(self, exits: list[str]) -> None:
        while self._exits_box_lay.count() > 0:
            item = self._exits_box_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._exit_btns.clear()
        for d in exits:
            btn = QPushButton(d)
            btn.setProperty("dirBtn", True)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda _=False, dd=d: self._exit_cmd(dd))
            self._exits_box_lay.addWidget(btn)
            self._exit_btns[d] = btn

    def _exit_cmd(self, direction: str) -> None:
        if self.session is not None:
            self.session.send(direction)

    # ---- walk 状态（nav.*）----
    def _nav_state(self, payload: dict) -> None:
        acc = payload.get("account")
        if acc is not None and self._account is not None and acc != self._account:
            return
        ev = payload.get("event", "")
        if ev == "nav.start":
            self.status.setText(f"行走中 ({payload.get('total', 0)} 步)")
            self.stop_btn.setEnabled(True)
        elif ev == "nav.step":
            self.status.setText(f"行走中 → {payload.get('step', '')}，剩 {len(payload.get('remaining', []) or [])} 步")
        elif ev == "nav.arrived":
            self.status.setText("已到达")
            self.stop_btn.setEnabled(False)
        elif ev in ("nav.stuck", "nav.stopped"):
            self.status.setText(f"停止: {payload.get('reason', '')}")
            self.stop_btn.setEnabled(False)

    # ---- 目的地：服务器 node 命令列表 ----
    _NODE_NAME_RE = re.compile(r"^\s*[★☆]?\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*$")
    _NODE_ROW_RE = re.compile(r"^\s*[│|]?\s*[★☆]?\s*[a-zA-Z_][a-zA-Z0-9_]*\s*[│|]")

    def _fetch_node_list(self, auto: bool = False, follow: str | None = None) -> None:
        """发送 `node` 开始捕获玩家路径表格（session 拦截表格行不上主输出）。

        auto=True（移动静止定时器触发）时：若用户正在交互（命令输入框聚焦、
        行走中、宏运行）则跳过本次刷新，避免静默命令打断用户操作；手动点
        刷新按钮不受限。follow="walk" 表示 node 捕获结束后自动接 walk 刷新
        （空闲联动两表一起更新）。
        """
        if self.session is None or not getattr(self.session, "logged_in", False):
            return
        if auto and self._user_busy():
            return
        if self._capturing or self._walk_capturing:
            return
        self._after_node = "walk" if follow == "walk" else None
        self._capturing = True
        self._seen_header = False
        self._node_pending = False
        self._node_foreign = False
        self._node_rows: list[tuple[str, str]] = []
        self.dest_list.clear()
        self.status.setText("正在读取 node 列表…")
        self._cap_timer.start()
        self.session.request_node()

    def _user_busy(self) -> bool:
        """用户正忙：命令输入框聚焦、导航行走中、宏运行 → 不静默刷新。"""
        try:
            from PyQt6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit
            fw = QApplication.focusWidget()
            if isinstance(fw, (QLineEdit, QPlainTextEdit, QTextEdit)):
                return True
        except Exception:
            pass
        nav = getattr(self.session, "navigator", None)
        if nav is not None and getattr(nav, "running", False):
            return True
        macros = getattr(self.session, "macros", None)
        if macros is not None and getattr(macros, "is_running", lambda: False)():
            return True
        return False

    def _capture_timeout(self) -> None:
        """捕获超时兜底：结束捕获，避免卡在捕获态；同时通知会话停止吞行。"""
        s = self.session
        if self._capturing:
            self._capturing = False
            self._seen_header = False
            self._cap_timer.stop()
            if s is not None:
                s.abort_node_capture()
            self.status.setText(f"node 读取超时（已获 {len(self._node_rows)} 条）")
            self._run_node_follow()
        elif self._walk_capturing:
            self._walk_capturing = False
            self._walk_seen_header = False
            self._walk_pending = False
            self._walk_foreign = False
            self._cap_timer.stop()
            if s is not None:
                s.abort_walk_capture()
            self.status.setText(f"walk 读取超时（已获 {len(self._walk_rows)} 条）")

    def _run_node_follow(self) -> None:
        """node 捕获结束后的联动刷新（空闲时自动接 walk）。"""
        if self._after_node != "walk":
            self._after_node = None
            return
        self._after_node = None
        if not self._walk_capturing and self.session is not None \
                and getattr(self.session, "logged_in", False):
            self._fetch_walk_list()

    def _on_text(self, payload: dict) -> None:
        acc = payload.get("account")
        if acc is not None and self._account is not None and acc != self._account:
            return
        line = str(payload.get("line") or "")
        if self._capturing:
            self._feed_node_line(line)
        if self._walk_capturing:
            self._feed_walk_line(line)

    def _feed_node_line(self, line: str) -> None:
        """解析 node 表格行（如 `│★ cj_yz  │扬州的中央广场  │...│`）。

        捕获起点：首行表头 `│名称  │目的地…`；清除起点：表尾 `└─…─┘` 行，
        或空路径提示「这里没有玩家定义的路径」。
        未见表头前：遇到普通消息行（世界/频道/临时讯息，无框线）只忽略，
        绝不终止捕获——否则 node 表格前的任何一条闲杂文本都会让整张表漏抓。
        """
        if not line.strip():
            return
        has_box = any(ch in line for ch in "│┌┐└┘├┤─")
        # 分页提示行（`== 未完继续 X% ==`）：到达即续命超时，等下一页数据
        if "未完继续" in line and "%" in line:
            self._cap_timer.start()
            return
        if "这里没有玩家定义的路径" in line:
            # 空路径提示：无框线、明确无表格，终止捕获
            self._capturing = False
            self._seen_header = False
            self._cap_timer.stop()
            self.status.setText("当前房间没有玩家定义的路径")
            self._run_node_follow()
            return
        if "└" in line and "─" in line:
            # 表尾框线，本次捕获结束（在表头门之前判断：表头页丢失时也能正常收尾）
            self._capturing = False
            self._seen_header = False
            self._cap_timer.stop()
            self.status.setText(f"node 路径 {len(self._node_rows)} 条")
            self._run_node_follow()
            return
        if self._seen_header is False:
            # 表头行或数据行兜底：表头页丢失（分页交互吞掉）时，首列
            # `[★☆]?ASCII名称│` 的数据行本身也能确认表格并开始解析。
            if has_box and "名称" in line and "目的地" in line:
                self._seen_header = True
            elif self._NODE_ROW_RE.match(line) is not None:
                self._seen_header = True
            else:
                return  # 未见表头：框线/表头/闲杂行一律忽略，继续等待表头
        if not has_box:
            return
        parts = [p for p in line.split("│") if p.strip()]
        if len(parts) < 2:
            return
        name_field = parts[0].strip()
        m = self._NODE_NAME_RE.match(name_field)
        if not m:
            return
        name = m.group(1)
        dest = parts[1].strip()
        if not dest:
            return
        self._node_rows.append((name, dest))
        item = QTreeWidgetItem([name, dest])
        self.dest_list.addTopLevelItem(item)
        self._cap_timer.start()  # 分页活动期续命：每次都重置超时，防止长表格中途被误杀

    def _send_node_walk(self, item=None) -> None:
        """发送 `node walk <名称>`：优先双击 item，否则用选中行，否则输入框文本。"""
        if self.session is None:
            return
        target = ""
        if item is not None:
            target = item.text(0)
        if not target:
            items = self.dest_list.selectedItems()
            if items:
                target = items[0].text(0)
        if not target:
            target = self.dest_ed.text().strip()
        if not target:
            return
        self.session.send(f"node walk {target}")
        self.status.setText(f"→ node walk {target}")
        self.stop_btn.setEnabled(True)

    def _on_stop(self) -> None:
        if self.session is not None and self.session.navigator is not None:
            self.session.navigator.stop()
        self.stop_btn.setEnabled(False)

    # ---- 内建路径：服务器 walk 命令列表 ----
    _WALK_ROW_RE = re.compile(r"^\s*[│|]\s*[^│]+\s*[│|]")

    def _fetch_walk_list(self) -> None:
        """发送 `walk` 开始捕获内建路径表（session 拦截表格行不上主输出）。"""
        if self.session is None or not getattr(self.session, "logged_in", False):
            return
        if self._walk_capturing or self._capturing:
            return
        self._walk_capturing = True
        self._walk_seen_header = False
        self._walk_pending = False
        self._walk_foreign = False
        self._walk_rows: list[tuple[str, str, str]] = []
        self.walk_list.clear()
        self.status.setText("正在读取 walk 列表…")
        self._cap_timer.start()
        self.session.request_walk()

    def _feed_walk_line(self, line: str) -> None:
        """解析 walk 表格行（`│目的地  │拼音  │步数  │`）。

        捕获起点：表头含「目的地/拼音/步数」或顶框线；表尾 `└─…─┘` 结束。
        说明行（walk 命令用法）不含竖线，忽略不上表。
        """
        if not line.strip():
            return
        has_vbar = "│" in line
        if "未完继续" in line and "%" in line:
            self._cap_timer.start()
            return
        if "没有内建路径" in line or "没有内建" in line or "你不能在这里定义路径" in line:
            self._walk_capturing = False
            self._walk_seen_header = False
            self._walk_pending = False
            self._walk_foreign = False
            self._cap_timer.stop()
            self.status.setText("当前房间没有内建路径")
            return
        if "内建路径出发点" in line and "不明确" in line:
            # 出发点不明确：服务器不会给出表格，直接终止，不等超时
            self._walk_capturing = False
            self._walk_seen_header = False
            self._walk_pending = False
            self._walk_foreign = False
            self._cap_timer.stop()
            if self.session is not None:
                self.session.abort_walk_capture()
            self.status.setText("当前区域的内建路径出发点暂时不明确")
            return
        if self._walk_foreign:
            # 外部表格（技能面板等）穿插在 walk 表格中：整段不上表，
            # 直到其表尾 `└…─…┘` 结束，回到 walk 表继续解析
            if "└" in line and "─" in line:
                self._walk_foreign = False
            return
        if "└" in line and "─" in line:
            # 表尾框线，本次捕获结束（在表头门之前判断：表头页丢失时也能正常收尾）
            self._walk_capturing = False
            self._walk_seen_header = False
            self._walk_pending = False
            self._walk_foreign = False
            self._cap_timer.stop()
            self.status.setText(f"walk 路径 {len(self._walk_rows)} 条")
            return
        if self._walk_seen_header is False:
            # 顶框线 `┌…─` 待确认：技能面板等其他表格也以 `┌…─` 开头，
            # 须下一行验证是真表头（含「目的地/拼音」）才进表，否则取消
            # 待确认并忽略（技能面板行不上表，避免误读进 walk 列表）。
            if self._walk_pending:
                if has_vbar and "目的地" in line and "拼音" in line:
                    self._walk_seen_header = True
                    self._walk_pending = False
                else:
                    self._walk_pending = False
                return
            if has_vbar and "目的地" in line and "拼音" in line:
                self._walk_seen_header = True
                return
            elif line.startswith("┌") and "─" in line:
                self._walk_pending = True
                return
            else:
                return
        if not has_vbar:
            if line.startswith("┌") and "─" in line:
                # walk 表格中出现新顶框线 = 外部表格（技能面板等）穿插开始
                self._walk_foreign = True
            return
        # 表头行（含「目的地/拼音/步数」列名）跳过，不当作数据
        if "目的地" in line and "拼音" in line and "步数" in line:
            return
        parts = [p for p in line.split("│") if p.strip()]
        if len(parts) < 3:
            return
        dest = parts[0].strip()
        pinyin = parts[1].strip()
        steps = parts[2].strip()
        if not dest or not pinyin:
            return
        self._walk_rows.append((dest, pinyin, steps))
        item = QTreeWidgetItem([dest, pinyin, steps])
        self.walk_list.addTopLevelItem(item)
        self._cap_timer.start()  # 分页活动期续命

    def _send_walk_cmd(self, item=None) -> None:
        """发送 `walk <拼音名>`：优先双击 item 列1（拼音），否则输入框文本。"""
        if self.session is None:
            return
        target = ""
        if item is not None:
            target = item.text(1)
        if not target:
            items = self.walk_list.selectedItems()
            if items:
                target = items[0].text(1)
        if not target:
            target = self.walk_ed.text().strip()
        if not target:
            return
        self.session.send(f"walk {target}")
        self.status.setText(f"→ walk {target}")
        self.stop_btn.setEnabled(True)