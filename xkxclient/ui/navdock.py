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
    移动静止 3 秒自动刷新 node 列表；`node` 表格行由 session 拦截不上主输出。
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
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(3000)
        self._idle_timer.timeout.connect(lambda: self._fetch_node_list(auto=True))
        self._cap_timer = QTimer(self)
        self._cap_timer.setSingleShot(True)
        self._cap_timer.setInterval(6000)
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
        self.go_btn = QPushButton("刷新")
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
        self.dest_list.itemDoubleClicked.connect(lambda _i, _c: self._send_node_walk())
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

        # ---- walk 状态 ----
        self.status = QLabel("待命中")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(6)
        lay.addWidget(self.node_frame)
        lay.addWidget(dest_frame, 1)
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
        self._fetch_node_list()

    def _unsub(self) -> None:
        if self._bus is not None:
            for event, sub in self._subs:
                self._bus.unsubscribe(event, sub)
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
        # 发生移动/换房：静止 3 秒后自动刷新 node 列表
        self._restart_idle_refresh()

    def _restart_idle_refresh(self) -> None:
        self._idle_timer.stop()
        if self.session is not None and getattr(self.session, "logged_in", False):
            self._idle_timer.start()

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
            npc = list(node.get("npc") or [])
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
        npc = list(node.get("npc") or [])
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

    def _fetch_node_list(self, auto: bool = False) -> None:
        """发送 `node` 开始捕获玩家路径表格（session 拦截表格行不上主输出）。

        auto=True（移动静止定时器触发）时：若用户正在交互（命令输入框聚焦、
        行走中、宏运行）则跳过本次刷新，避免静默命令打断用户操作；手动点
        刷新按钮不受限。
        """
        if self.session is None or not getattr(self.session, "logged_in", False):
            return
        if auto and self._user_busy():
            return
        if self._capturing:
            return
        self._capturing = True
        self._seen_header = False
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
        """捕获超时兜底：结束捕获，避免卡在捕获态。"""
        if self._capturing:
            self._capturing = False
            self._seen_header = False
            self._cap_timer.stop()
            self.status.setText(f"node 读取超时（已获 {len(self._node_rows)} 条）")

    def _on_text(self, payload: dict) -> None:
        if not self._capturing:
            return
        acc = payload.get("account")
        if acc is not None and self._account is not None and acc != self._account:
            return
        self._feed_node_line(str(payload.get("line") or ""))

    def _feed_node_line(self, line: str) -> None:
        """解析 node 表格行（如 `│★ cj_yz  │扬州的中央广场  │...│`）。

        捕获起点：首行表头 `│名称  │目的地…`；清除起点：表尾 `└─…─┘` 行。
        """
        if not line.strip():
            return
        has_box = any(ch in line for ch in "│┌┐└┘├┤─")
        if self._seen_header is False:
            if not has_box:
                # 空路径提示（无框线）或完全不是表格：终止捕获
                self._capturing = False
                self._cap_timer.stop()
                self.status.setText(
                    "当前房间没有玩家定义的路径" if "这里没有玩家定义的路径" in line
                    else "node 列表为空或不可用"
                )
                return
            if "名称" not in line or "目的地" not in line:
                # 起始框线/空行，继续等表头
                return
            self._seen_header = True
            return
        if "└" in line and "─" in line:
            # 表尾框线，本次捕获结束
            self._capturing = False
            self._seen_header = False
            self._cap_timer.stop()
            self.status.setText(f"node 路径 {len(self._node_rows)} 条")
            return
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

    def _send_node_walk(self) -> None:
        """发送 `node walk <名称>`：取选中行名，否则用输入框文本。"""
        if self.session is None:
            return
        target = ""
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