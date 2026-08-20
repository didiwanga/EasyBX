from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, QTimer, Qt
from PyQt6.QtGui import QIcon, QKeySequence, QTransform
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .navdock import FlowLayout

# B9 预设快捷动作
_QUICK_PRESETS = [
    ("详情", "score"), ("技能", "skills"), ("状态", "hp"),
    ("背包", "inventory"), ("逛逛", "wander"),
]


class QuickActionsDock(QWidget):
    """B9 快捷动作：按钮形式（标签只写作用），预设 + 用户自定义（本地配置）。
    支持拖拽按钮调整布局顺序（顺序持久化到 config `quick_actions_order`）。"""

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setMinimumWidth(120)
        # 允许横向伸缩：浮动 dock 缩放时内容跟随窗口宽度
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(QLabel("快捷动作（拖拽可排序）"))

        # 按钮区放进滚动区：高度不足时竖向滚动，按钮不被遮蔽
        self._flow_host = QWidget()
        self._flow_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._flow_host_lay = QVBoxLayout(self._flow_host)
        self._flow_host_lay.setContentsMargins(0, 0, 0, 0)
        self.btn_flow = FlowLayout(hspacing=4, vspacing=4)
        self._flow_host_lay.addLayout(self.btn_flow)
        self._flow_host.installEventFilter(self)

        self.scroll = QScrollArea(self)
        self.scroll.setWidget(self._flow_host)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        lay.addWidget(self.scroll, 1)

        self.add_btn = QPushButton("+ 添加")
        self.add_btn.clicked.connect(self._on_add)
        lay.addWidget(self.add_btn)
        self._custom_btns: dict[QPushButton, int] = {}
        self._drag_btn: QPushButton | None = None   # 当前拖拽的按钮
        self._drag_start: object | None = None      # 按下时鼠标位置
        self._dragging = False
        self._applying_width = False
        self._rebuild()

    def _btn_width(self) -> int:
        """按钮自适应宽度：横排铺满可用宽度，不残留右侧空白。

        规则：按钮宽度在 [最小(3中文), 最大(8中文)] 之间；随窗口变宽而增大，
        达到 8 中文上限后，若可用宽度已能再容纳一个最小宽按钮，则缩回 8 中文
        并让横排多排一个按钮（每排至少 2 个）。
        """
        fm = self.fontMetrics()
        max_w = fm.horizontalAdvance("中中中中中中中中") + 24
        min_w = fm.horizontalAdvance("中中中") + 16
        spacing = 4
        avail = self.width() - 16
        if avail < 2 * min_w + spacing:
            return max(min_w, (avail - spacing) // 2)
        n = 2
        while True:
            w = (avail - (n - 1) * spacing) // n
            if w >= max_w:
                # 本排 n 个按钮即使到 8 中文也放不满当前行：
                # 若能再容纳一个最小宽按钮则加列，否则固定为 8 中文
                if avail >= (n + 1) * min_w + n * spacing:
                    n += 1
                    continue
                return max_w
            if w <= min_w:
                # 本排放不下 n 个最小宽按钮：减少列数（至少 2）
                if n > 2:
                    n -= 1
                    continue
                return max(min_w, w)
            return w

    def bind(self, session) -> None:
        self.session = session
        self._rebuild()

    # ---- 顺序持久化 ----
    def _load_order(self, n_custom: int) -> list[str]:
        """按钮顺序 key 列表：p:{i} 预设 / c:{j} 自定义。已保存的排前，缺失的按默认追加。"""
        saved = []
        raw = self.session.app.config.get("quick_actions_order") if self.session else None
        if isinstance(raw, list):
            saved = [str(k) for k in raw]
        order: list[str] = []
        seen: set[str] = set()
        for k in saved:
            if k in seen:
                continue
            if k.startswith("c:"):
                try:
                    if 0 <= int(k[2:]) < n_custom:
                        order.append(k); seen.add(k)
                except ValueError:
                    continue
            elif k.startswith("p:"):
                try:
                    if 0 <= int(k[2:]) < len(_QUICK_PRESETS):
                        order.append(k); seen.add(k)
                except ValueError:
                    continue
        for i in range(len(_QUICK_PRESETS)):
            k = f"p:{i}"
            if k not in seen:
                order.append(k); seen.add(k)
        for j in range(n_custom):
            k = f"c:{j}"
            if k not in seen:
                order.append(k); seen.add(k)
        return order

    def _save_order(self, order: list[str]) -> None:
        if self.session is not None:
            self.session.app.config.set("quick_actions_order", list(order))

    def _rebuild(self) -> None:
        while self.btn_flow.count():
            item = self.btn_flow.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._custom_btns = {}
        custom = []
        if self.session is not None:
            raw = self.session.app.config.get("quick_actions")
            if isinstance(raw, list):
                custom = [r for r in raw if isinstance(r, list) and len(r) == 2]
        order = self._load_order(len(custom))
        bw = self._btn_width()
        by_key: dict[str, QPushButton] = {}
        for i, (label, cmd) in enumerate(_QUICK_PRESETS):
            btn = QPushButton(label)
            btn.setToolTip(cmd)
            btn.setFixedWidth(bw)
            btn.clicked.connect(lambda _=False, c=cmd: self._fire(c))
            by_key[f"p:{i}"] = btn
        for j, row in enumerate(custom):
            btn = QPushButton(str(row[0]))
            btn.setToolTip(str(row[1]))
            btn.setFixedWidth(bw)
            btn.clicked.connect(lambda _=False, c=str(row[1]): self._fire(c))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda _pos, b=btn, idx=j: self._show_btn_menu(b, idx))
            self._custom_btns[btn] = j
            by_key[f"c:{j}"] = btn
        for k in order:
            btn = by_key.get(k)
            if btn is None:
                continue
            btn.installEventFilter(self)
            self.btn_flow.addWidget(btn)
        self._apply_btn_widths()

    def _apply_btn_widths(self) -> None:
        """按当前可用宽度刷新所有按钮宽度（每排至少 2 个、最大 8 中文）。"""
        if getattr(self, "_applying_width", False):
            return
        self._applying_width = True
        try:
            bw = self._btn_width()
            for i in range(self.btn_flow.count()):
                item = self.btn_flow.itemAt(i)
                w = item.widget() if item else None
                if isinstance(w, QPushButton):
                    w.setFixedWidth(bw)
        finally:
            self._applying_width = False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 延迟到子控件布局完成后，按实际按钮区宽度刷新按钮尺寸
        QTimer.singleShot(0, self._apply_btn_widths)

    # ---- 拖拽排序 ----
    def eventFilter(self, obj, event) -> bool:
        if obj is self._flow_host and event.type() == QEvent.Type.Resize:
            # 按钮区容器宽度变化：刷新按钮宽度
            self._apply_btn_widths()
            return super().eventFilter(obj, event)
        if not isinstance(obj, QPushButton):
            return super().eventFilter(obj, event)
        et = event.type()
        if et == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_btn = obj
                self._drag_start = event.position()
                self._dragging = False
            return super().eventFilter(obj, event)
        if et == QEvent.Type.MouseMove:
            if obj is self._drag_btn and self._drag_start is not None and not self._dragging:
                d = event.position() - self._drag_start
                if d.manhattanLength() > QApplication.startDragDistance():
                    self._dragging = True
            if self._dragging:
                return True   # 拖拽期间吞掉移动事件，避免干扰
            return super().eventFilter(obj, event)
        if et == QEvent.Type.MouseButtonRelease:
            if obj is self._drag_btn and self._dragging:
                # 延迟到事件处理结束后再重建，避免拖拽期间删除正在处理事件的控件导致闪退
                QTimer.singleShot(0, lambda b=obj, p=event.globalPosition().toPoint(): self._drop_at(b, p))
                self._drag_btn = None
                self._drag_start = None
                self._dragging = False
                return True   # 吞掉释放，避免触发按钮点击
            if obj is self._drag_btn:
                self._drag_btn = None
                self._drag_start = None
                self._dragging = False
            return super().eventFilter(obj, event)
        return super().eventFilter(obj, event)

    def _drop_at(self, btn: QPushButton, global_pos) -> None:
        """把 btn 插入到鼠标释放时所在按钮的位置，并持久化顺序。"""
        target = self._btn_at(global_pos)
        if target is None or target is btn:
            return
        custom = []
        if self.session is not None:
            raw = self.session.app.config.get("quick_actions")
            if isinstance(raw, list):
                custom = [r for r in raw if isinstance(r, list) and len(r) == 2]
        order = self._load_order(len(custom))
        try:
            si = order.index(self._key_of(btn))
            di = order.index(self._key_of(target))
        except ValueError:
            return
        if si == di:
            return
        k = order.pop(si)
        di2 = order.index(self._key_of(target))   # pop 后重新定位
        order.insert(di2, k)
        self._save_order(order)
        self._rebuild()

    def _btn_at(self, global_pos) -> QPushButton | None:
        for i in range(self.btn_flow.count()):
            item = self.btn_flow.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, QPushButton):
                if w.rect().contains(w.mapFromGlobal(global_pos)):
                    return w
        return None

    def _key_of(self, btn: QPushButton) -> str:
        for b, j in self._custom_btns.items():
            if b is btn:
                return f"c:{j}"
        return self._key_of_preset(btn)

    @staticmethod
    def _key_of_preset(btn: QPushButton) -> str:
        for i, (label, _cmd) in enumerate(_QUICK_PRESETS):
            if btn.text() == label:
                return f"p:{i}"
        return ""

    def _show_btn_menu(self, btn: QPushButton, idx: int) -> None:
        """右键菜单：编辑 / 删除用户自定义按钮。"""
        if self.session is None:
            return
        menu = QMenu(self)
        menu.addAction("编辑该按钮", lambda: self._edit_btn(idx))
        menu.addAction("删除该按钮", lambda: self._delete_btn(idx))
        menu.addSeparator()
        menu.addAction("取消")
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _edit_btn(self, idx: int) -> None:
        if self.session is None:
            return
        custom = [r for r in (self.session.app.config.get("quick_actions") or [])
                  if isinstance(r, list) and len(r) == 2]
        if not (0 <= idx < len(custom)):
            return
        name = str(custom[idx][0])
        cmd = str(custom[idx][1])
        res = self._dlg_action(name, cmd, title="编辑快捷动作")
        if res:
            custom[idx] = [res[0], res[1]]
            self.session.app.config.set("quick_actions", custom)
            self._rebuild()

    def _delete_btn(self, idx: int) -> None:
        if self.session is None:
            return
        custom = [r for r in (self.session.app.config.get("quick_actions") or [])
                  if isinstance(r, list) and len(r) == 2]
        if 0 <= idx < len(custom):
            custom.pop(idx)
        self.session.app.config.set("quick_actions", custom)
        # 清理顺序里失效的自定义 key（索引后移，旧 c:{idx} 应删除）
        order = [k for k in (self.session.app.config.get("quick_actions_order") or [])
                 if not (isinstance(k, str) and k.startswith("c:"))]
        order.extend(f"c:{j}" for j in range(len(custom)))
        self.session.app.config.set("quick_actions_order", order)
        self._rebuild()

    def _fire(self, cmd: str) -> None:
        if self.session is not None:
            self.session.send(cmd)

    def _dlg_action(self, name: str = "", cmd: str = "", title: str = "快捷动作") -> tuple[str, str] | None:
        """名称 + 命令 输入对话框；返回 (名称, 命令)，取消返回 None。"""
        from PyQt6.QtWidgets import QDialog, QFormLayout, QLineEdit
        from PyQt6.QtWidgets import QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        name_ed, cmd_ed = QLineEdit(), QLineEdit()
        name_ed.setText(name)
        cmd_ed.setText(cmd)
        form = QFormLayout()
        form.addRow("名称", name_ed)
        form.addRow("命令", cmd_ed)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dlg)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addLayout(form)
        lay.addWidget(box)
        if dlg.exec() and name_ed.text().strip() and cmd_ed.text().strip():
            return (name_ed.text().strip(), cmd_ed.text().strip())
        return None

    def _on_add(self) -> None:
        if self.session is None:
            return
        res = self._dlg_action(title="添加快捷动作")
        if res:
            custom = list(self.session.app.config.get("quick_actions") or [])
            custom.append([res[0], res[1]])
            self.session.app.config.set("quick_actions", custom)
            self._rebuild()


_DIR_BUTTONS = [
    ["nw", "n", "ne"],
    ["w", "look", "e"],
    ["sw", "s", "se"],
]
_ROW2 = ["u", "d", "enter", "out"]

_EIGHT_DIRS = {"n", "s", "e", "w", "ne", "nw", "se", "sw"}


def _dir_icon(name: str) -> QIcon:
    """方向图标：用 Qt 原生箭头，旋转得到八方向（避免 emoji）。"""
    style = QApplication.style()
    base = style.standardIcon(QStyle.StandardPixmap.SP_ArrowUp)
    pix = base.pixmap(18, 18)
    if pix.isNull():
        return QIcon()
    angle = {"n": 0, "ne": 45, "e": 90, "se": 135,
             "s": 180, "sw": 225, "w": 270, "nw": 315}[name]
    if angle:
        t = QTransform().rotate(angle)
        pix = pix.transformed(t, Qt.TransformationMode.SmoothTransformation)
    return QIcon(pix)


_EXIT_SHORT = {
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
    "up": "u", "down": "d",
}

_EXIT_SUFFIXES = ("up", "down")


def normalize_exits(exits: list[str]) -> dict[str, str]:
    """出口名 → 方向短名，解析 eastdown/southup 等复合出口（取基础八方向）。"""
    mapped: dict[str, str] = {}
    for x in exits or []:
        if x in _EXIT_SHORT:
            mapped.setdefault(_EXIT_SHORT[x], x)
        else:
            base, suf = x, None
            for k in _EXIT_SUFFIXES:
                if x.endswith(k):
                    base, suf = x[:-len(k)], k
                    break
            if suf and base in _EXIT_SHORT and _EXIT_SHORT[base] in _EIGHT_DIRS:
                mapped.setdefault(_EXIT_SHORT[base], x)
            else:
                mapped.setdefault(x, x)
    return mapped


_ARROW_SHORT = {
    "north": "n", "northeast": "ne", "northwest": "nw",
    "south": "s", "southeast": "se", "southwest": "sw",
    "east": "e", "west": "w",
    "northup": "nu", "northdown": "nd",
    "southup": "su", "southdown": "sd",
    "eastup": "eu", "eastdown": "ed",
    "westup": "wu", "westdown": "wd",
    "up": "up", "down": "down", "enter": "enter", "out": "out",
}


def arrow_short(x: str) -> str:
    """完整出口名 → 方向键短键：保留上下合成标记(nu/nd/su/sd/eu/ed/wu/wd/neu…)。"""
    if x in _ARROW_SHORT:
        return _ARROW_SHORT[x]
    for suf in _EXIT_SUFFIXES:
        if x.endswith(suf):
            base = x[:-len(suf)]
            if base in _EXIT_SHORT and _EXIT_SHORT[base] in _EIGHT_DIRS:
                return _EXIT_SHORT[base] + suf[0]
    return x


_ARROW_CHAIN = {
    Qt.Key.Key_Up: ("n", "nw", "ne", "nu", "nd", "up", "enter"),
    Qt.Key.Key_Down: ("s", "sw", "se", "su", "sd", "down", "out"),
    Qt.Key.Key_Left: ("w", "nw", "sw", "wu", "wd"),
    Qt.Key.Key_Right: ("e", "ne", "se", "eu", "ed"),
}


def arrow_move(exits: list[str], key) -> str | None:
    """逐键顺序候选：按该方向键固定链取第一个真实出口（如 上=n→nw→ne→nu→nd→up→enter）；
    无匹配返回 None。返回真实出口全名。"""
    chain = _ARROW_CHAIN.get(key)
    if not chain:
        return None
    avail = {arrow_short(x): x for x in exits or []}
    for cand in chain:
        if cand in avail:
            return avail[cand]
    return None


class MoveControlDock(QWidget):
    """B9 移动控制：3×3 方向格 + up/down/enter/out + 其他出口动态按钮，由 GMCP.Move 的 exits 驱动。"""

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self._subs: list = []
        self.setMinimumWidth(170)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)   # 各区块间（3×3 与上下进出按钮）间距同按钮间距
        self.room_label = QLabel("当前位置: -")
        self.room_label.setWordWrap(True)
        lay.addWidget(self.room_label)
        grid = QGridLayout()
        grid.setSpacing(8)   # 行/列间距统一（多余宽度由居中容器吸收，恒定不随 dock 宽变化）
        self._btns: dict[str, QPushButton] = {}
        for r, row in enumerate(_DIR_BUTTONS):
            for c, name in enumerate(row):
                if name == "look":
                    btn = QPushButton("看")
                    btn.clicked.connect(lambda _=False, d="look": self._move(d))
                else:
                    btn = QPushButton(_dir_icon(name), "")
                    btn.setIconSize(QSize(18, 18))
                    btn.clicked.connect(lambda _=False, d=name: self._move(d))
                btn.setProperty("dirBtn", True)
                btn.setFixedSize(42, 32)
                # 小键盘快捷键：3×3 位置（左上 nw=7，中 look=5，右下 se=3）：
                #     nw n ne → 7 8 9
                #     w look e → 4 5 6
                #     sw s se → 1 2 3
                kp_num = (2 - r) * 3 + (c + 1)
                btn.setShortcut(QKeySequence(
                    Qt.KeyboardModifier.KeypadModifier.value | (Qt.Key.Key_0.value + kp_num)))
                grid.addWidget(btn, r, c)
                if name != "look":
                    self._btns[name] = btn
        # 水平居中容器：吸收 dock 多余宽度，让 3×3 列距恒定 4px（不随 dock 宽伸展）
        grid_host = QHBoxLayout()
        grid_host.setSpacing(0)
        grid_host.addStretch(1)
        grid_host.addLayout(grid)
        grid_host.addStretch(1)
        lay.addLayout(grid_host)
        row2 = QHBoxLayout()
        row2.setSpacing(8)   # 与 3×3 按钮间距一致
        for name in _ROW2:
            btn = QPushButton({"u": "上", "d": "下", "enter": "进", "out": "出"}[name])
            btn.setProperty("dirBtn", True)
            btn.setFixedSize(42, 32)   # 宽度与 3×3 方向按钮一致
            btn.clicked.connect(lambda _=False, d=name: self._move(d))
            self._btns[name] = btn
            row2.addWidget(btn)
        # 水平居中容器：上下进出按钮与 3×3 同宽居中，间距恒定；
        # 顶部额外 8px，使 3×3 与四键行间距 = lay 8px + margin 8px = 16px（加倍）
        row2_host = QHBoxLayout()
        row2_host.setSpacing(0)
        row2_host.setContentsMargins(0, 8, 0, 0)
        row2_host.addStretch(1)
        row2_host.addLayout(row2)
        row2_host.addStretch(1)
        lay.addLayout(row2_host)

        # 其他出口区：除八方向/上下/进出外的出口（含纯数字出口）动态按钮，
        # 有几个显示几个；无此类出口时整区隐藏。
        self._extra_group = QWidget(self)
        eg = QVBoxLayout(self._extra_group)
        eg.setContentsMargins(0, 2, 0, 0)
        eg.setSpacing(2)
        self._extra_label = QLabel("其他出口")
        self._extra_label.setStyleSheet("color:#808080; font-size:11px;")
        self._extra_lay = FlowLayout(hspacing=4, vspacing=4)
        eg.addWidget(self._extra_label)
        eg.addLayout(self._extra_lay)
        self._extra_btns: list[QPushButton] = []
        self._extra_group.hide()
        lay.addWidget(self._extra_group)

        lay.addStretch(1)
        self._cur_exits: list[str] = []
        self._btn_exit: dict[str, str] = {}
        self.set_exits([])

    def bind(self, session) -> None:
        self._unsub()
        self.session = session
        self.set_exits(getattr(session, "exits", []) or [])
        bus = getattr(getattr(session, "app", None), "bus", None)
        if bus is not None:
            for ev in ("state.room", "GMCP.Move", "map.pushed"):
                sub = bus.subscribe(ev, self._on_event)
                self._subs.append((ev, sub))
        self._sync_room(getattr(session, "room_name", "") or "")

    def _unsub(self) -> None:
        bus = getattr(getattr(self.session, "app", None), "bus", None)
        if bus is not None:
            for ev, sub in self._subs:
                try:
                    bus.unsubscribe(ev, sub)
                except Exception:
                    pass
        self._subs = []

    def _on_event(self, payload: dict) -> None:
        acc = payload.get("account")
        if acc is not None and getattr(self.session, "account_id", None) not in (None, acc):
            return
        ev = payload.get("event", "")
        name = ""
        if ev == "state.room":
            name = str(payload.get("name") or "")
        elif ev == "GMCP.Move":
            d = payload.get("data")
            if not isinstance(d, dict):
                d = {}
            if str(d.get("result", "")).lower() in ("true", "1",):
                name = str(d.get("short") or "")
        elif ev == "map.pushed":
            cache = getattr(self.session, "map_cache", None)
            if cache is not None:
                name = getattr(cache, "current", "") or ""
        if name:
            self._sync_room(name)

    def _sync_room(self, name: str) -> None:
        if name:
            self.room_label.setText(f"当前位置: {name}")

    def _move(self, d: str) -> None:
        if self.session is None:
            return
        if d in _EIGHT_DIRS:
            d = self._btn_exit.get(d, d)
        if d == "look":
            self.session.connection.send_line("look")
        else:
            self.session.send(d)

    def _move_extra(self, name: str) -> None:
        if self.session is None:
            return
        self.session.send(name)

    def _clear_extra(self) -> None:
        while self._extra_lay.count():
            it = self._extra_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._extra_btns = []

    def _refresh_extras(self) -> None:
        """其他出口区重建：显示未映射到八方向/u/d/enter/out 的出口按钮。"""
        self._clear_extra()
        covered = set(_EIGHT_DIRS) | {"u", "d", "enter", "out"}
        mapped_shorts: dict[str, str] = {}
        for short, full in normalize_exits(self._cur_exits).items():
            mapped_shorts[full] = short
        extras = [x for x in self._cur_exits if mapped_shorts.get(x, x) not in covered]
        if not extras:
            self._extra_group.hide()
            return
        for x in extras:
            btn = QPushButton(x)
            btn.setProperty("dirBtn", True)
            btn.setToolTip(f"出口 {x}")
            btn.clicked.connect(lambda _=False, d=x: self._move_extra(d))
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._extra_btns.append(btn)
            self._extra_lay.addWidget(btn)
        self._extra_group.show()

    def set_exits(self, exits: list[str]) -> None:
        self._cur_exits = list(exits or [])
        self._btn_exit = {}
        avail: set[str] = set()
        if self.session is not None and self.session.connected and self.session.logged_in:
            for short, full in normalize_exits(self._cur_exits).items():
                avail.add(short)
                self._btn_exit.setdefault(short, full)
        for name, btn in self._btns.items():
            btn.setEnabled(name in avail)
        self._refresh_extras()


class MacroControlDock(QWidget):
    """B9 宏控制：宏列表 + 运行/暂停/恢复/停止 + 步骤进度。"""

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.list = QListWidget()
        self.run_btn = QPushButton("▶ 运行")
        self.pause_btn = QPushButton("⏸ 暂停")
        self.resume_btn = QPushButton("▶ 继续")
        self.stop_btn = QPushButton("■ 停止")
        self.status = QLabel("未运行")
        self.list.itemDoubleClicked.connect(self._run_current)
        self.run_btn.clicked.connect(self._run_current)
        self.pause_btn.clicked.connect(self._on_pause)
        self.resume_btn.clicked.connect(self._on_resume)
        self.stop_btn.clicked.connect(self._on_stop)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(QLabel("宏控制"))
        lay.addWidget(self.list, 1)
        btns = QHBoxLayout()
        btns.addWidget(self.run_btn)
        btns.addWidget(self.pause_btn)
        btns.addWidget(self.resume_btn)
        btns.addWidget(self.stop_btn)
        lay.addLayout(btns)
        lay.addWidget(self.status)
        self._subs = []
        self._sync(idle=True)

    def bind(self, session) -> None:
        self._unsub()
        self.session = session
        if session is not None:
            bus = getattr(session, "app", None)
            bus = getattr(bus, "bus", None) if bus is not None else None
            if bus is None:
                bus = getattr(session, "bus", None)
            if bus is not None:
                acc = session.account_id
                for ev in ("macro.start", "macro.stop", "macro.end", "macro.step", "macro.state"):
                    sub = bus.subscribe(ev, lambda p, e=ev: self._on_event(p, acc, e))
                    self._subs.append((ev, sub))
                # 登录完成兜底刷新：切换用户后宏列表/按钮立即同步到当前账号，
                # 不必等宏有动作
                sub = bus.subscribe("login.done", lambda p: self._on_login_done(p, acc))
                self._subs.append(("login.done", sub))
                # 宏编辑器保存后实时刷新宏列表
                sub = bus.subscribe("automation.saved",
                                    lambda p: self._on_saved(p, acc))
                self._subs.append(("automation.saved", sub))
        self.reload()
        self._sync(idle=True)

    def _on_login_done(self, payload: dict, acc: str) -> None:
        if (payload.get("account") or "") != acc:
            return
        self.reload()
        self._sync(idle=True)

    def _on_saved(self, payload: dict, acc: str) -> None:
        if (payload.get("account") or "") != acc:
            return
        if (payload.get("kind") or "macros") != "macros":
            return
        self.reload()

    def _unsub(self) -> None:
        if self.session is not None:
            bus = getattr(self.session, "app", None)
            bus = getattr(bus, "bus", None) if bus is not None else None
            if bus is None:
                bus = getattr(self.session, "bus", None)
            if bus is not None:
                for ev, sub in self._subs:
                    try:
                        bus.unsubscribe(ev, sub)
                    except Exception:
                        pass
        self._subs = []

    def _on_event(self, payload: dict, acc: str, ev: str) -> None:
        if (payload.get("account") or "") != acc:
            return
        name = payload.get("name") or ""
        if ev == "macro.step":
            self.status.setText(f"运行: {name}  步骤 {payload.get('index')}/{payload.get('total')}")
            self._sync(running=True, paused=False)
        elif ev == "macro.state":
            st = payload.get("state")
            if st == "paused":
                self.status.setText(f"已暂停: {name}")
                self._sync(running=True, paused=True)
            elif st == "running":
                self.status.setText(f"运行中: {name}")
                self._sync(running=True, paused=False)
            elif st == "waiting_trigger":
                self.status.setText(f"等待触发: {name}")
        elif ev == "macro.start":
            self.status.setText(f"运行: {name}")
            self._sync(running=True, paused=False)
        elif ev in ("macro.end", "macro.stop"):
            self.status.setText("未运行")
            self._sync(idle=True)

    def reload(self) -> None:
        self.list.clear()
        if self.session is None:
            return
        for name in self.session.macros.list():
            self.list.addItem(name)

    def _run_current(self) -> None:
        if self.session is None:
            return
        item = self.list.currentItem()
        if item:
            self._run(item.text())

    def _run(self, name: str) -> None:
        if self.session is None:
            return
        ok = self.session.macros.start(name)
        self.status.setText(f"运行: {name}" if ok else f"启动失败: {name}")
        self._sync(running=ok, paused=False)

    def _on_pause(self) -> None:
        if self.session is None:
            return
        self.session.macros.pause()
        self._sync(running=True, paused=True)

    def _on_resume(self) -> None:
        if self.session is None:
            return
        self.session.macros.resume()
        self._sync(running=True, paused=False)

    def _on_stop(self) -> None:
        if self.session is None:
            return
        self.session.macros.stop()
        self.status.setText("已停止")
        self._sync(idle=True)

    def _sync(self, idle: bool = False, running: bool = False, paused: bool = False) -> None:
        """按钮可用性：idle 仅运行；运行中 暂停+停止；暂停中 继续+停止。
        停止按钮常亮：任何状态下点击都强制停止宏并清空积压命令。"""
        self.run_btn.setEnabled(idle)
        self.pause_btn.setEnabled(running and not paused)
        self.resume_btn.setEnabled(running and paused)
        self.stop_btn.setEnabled(True)

    def set_progress(self, text: str) -> None:
        self.status.setText(text)


class MacroRecorderDock(QWidget):
    """B3b 5b' 宏录制 dock：开始/暂停/恢复/停止；采集命令框+快捷动作+移动控制命令；
    仅錄制移动；实时编辑步骤；命令合并到单个命令步骤（; 分隔），实时插入后新步骤另起。
    """

    _MOVE_WORDS = {
        "north", "south", "east", "west", "northeast", "northwest",
        "southeast", "southwest", "up", "down", "enter", "out",
        "northup", "northdown", "southup", "southdown",
        "eastup", "eastdown", "westup", "westdown",
        "northeastup", "northeastdown", "northwestup", "northwestdown",
        "southeastup", "southeastdown", "southwestup", "southwestdown",
        "n", "s", "e", "w", "ne", "nw", "se", "sw", "u", "d",
    }

    def __init__(self, session=None, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.name_ed = QLineEdit()
        self.name_ed.setPlaceholderText("宏名称")
        self.mov_only = QPushButton("仅移动"); self.mov_only.setCheckable(True)
        self.rec_btn = QPushButton("● 开始录制")
        self.pause_btn = QPushButton("⏸ 暂停")
        self.resume_btn = QPushButton("▶ 继续")
        self.stop_btn = QPushButton("■ 停止并保存")
        self.step_list = QListWidget()
        self.ins_btn = QPushButton("＋插入步骤")
        self.del_btn = QPushButton("－删除选中")
        self.ins_btn.clicked.connect(self._on_insert)
        self.del_btn.clicked.connect(self._on_del_step)
        self.rec_btn.clicked.connect(self._on_start)
        self.pause_btn.clicked.connect(self._on_pause)
        self.resume_btn.clicked.connect(self._on_resume)
        self.stop_btn.clicked.connect(self._on_stop)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(QLabel("宏录制"))
        row0 = QHBoxLayout(); row0.addWidget(self.name_ed, 1); row0.addWidget(self.mov_only)
        lay.addLayout(row0)
        btns = QHBoxLayout()
        btns.addWidget(self.rec_btn); btns.addWidget(self.pause_btn)
        btns.addWidget(self.resume_btn); btns.addWidget(self.stop_btn)
        lay.addLayout(btns)
        lay.addWidget(self.step_list, 1)
        edbt = QHBoxLayout(); edbt.addWidget(self.ins_btn); edbt.addWidget(self.del_btn)
        lay.addLayout(edbt)

        self._recording = False
        self._paused = False
        self._steps: list[dict] = []
        self._after_insert = False   # 实时插入后，后续命令进入新的命令步骤
        self._sync()

    # ---- 状态 ----
    def bind(self, session) -> None:
        self.session = session
        if session is not None:
            session._macro_recorder = self._record
            if self._recording:
                session._macro_recording = True

    def _sync(self) -> None:
        self.rec_btn.setEnabled(not self._recording)
        self.pause_btn.setEnabled(self._recording and not self._paused)
        self.resume_btn.setEnabled(self._recording and self._paused)
        self.stop_btn.setEnabled(self._recording)
        self.name_ed.setEnabled(not self._recording)
        self.mov_only.setEnabled(not self._recording)
        self.ins_btn.setEnabled(self._recording)
        self.del_btn.setEnabled(self._recording)

    def _start(self, name: str) -> None:
        if self.session is None:
            return
        self._steps = []
        self._after_insert = False
        self._recording = True
        self._paused = False
        self.session._macro_recording = True
        self._refresh()
        self._sync()

    def _stop(self) -> None:
        if self.session is None:
            return
        name = self.name_ed.text().strip()
        if not name:
            name = "macro_rec"
        steps = [dict(s) for s in self._steps if s]
        cfg = self.session.app.config
        # 只更新账号自有宏；共享宏保留在共享文件，避免被误写入账号文件
        data = {d["name"]: dict(d) for d in cfg.automation(self.session.account_id)["macros"]
                if not d.get("shared")}
        data[name] = {"name": name, "enabled": True, "shared": False, "steps": steps,
                      "group": "", "desc": ""}
        cfg.save_automation(self.session.account_id, "macros", list(data.values()))
        self.session.reload_automation()
        self.session.app.bus.publish("automation.saved", account=self.session.account_id, kind="macros")
        self._recording = False
        self._paused = False
        self.session._macro_recording = False
        self._refresh()
        self._sync()
        self.session.app.bus.publish("ui.message", account=self.session.account_id,
                                     message=f"宏「{name}」已录制保存")

    # ---- 录制采集（session.send 回调）----
    def __call__(self, text: str) -> None:
        self._record(text)

    def _record(self, text: str) -> None:
        if not self._recording or self._paused or self.session is None:
            return
        text = text.strip()
        if not text:
            return
        if not self.mov_only.isChecked():
            self._feed(text)
            return
        # 仅绘制移动：单命令且为移动方向
        cmds = [c.strip() for c in text.split(";") if c.strip()]
        if len(cmds) == 1 and cmds[0] in self._MOVE_WORDS:
            self._feed(cmds[0])

    def _feed(self, cmd: str) -> None:
        """命令合并到当前命令步骤；用户实时插入过步骤则新开命令步骤。"""
        if self._after_insert or not self._steps or self._steps[-1].get("type") != "cmd":
            self._steps.append({"type": "cmd", "command": cmd})
            self._after_insert = False
            self._refresh()
            return
        last = self._steps[-1]
        cur = (last.get("command") or "").strip(";")
        last["command"] = (cur + ";" + cmd) if cur else cmd
        self._refresh()

    # ---- 实时编辑 ----
    def _on_insert(self) -> None:
        if not self._recording:
            return
        from xkxclient.ui.editors import StepDialog
        default = self._steps[-1] if self._steps else None
        dlg = StepDialog(self._steps, default, self)
        if dlg.exec() and dlg.result_step():
            row = self.step_list.currentRow()
            idx = row if row >= 0 else len(self._steps)
            self._steps.insert(idx, dlg.result_step())
            self._after_insert = True   # 之后录制进入新的命令步骤
            self._refresh()

    def _on_del_step(self) -> None:
        row = self.step_list.currentRow()
        if self._recording and row >= 0:
            self._steps.pop(row)
            self._refresh()

    def _on_pause(self) -> None:
        if self._recording:
            self._paused = True
            self._sync()

    def _on_resume(self) -> None:
        if self._recording:
            self._paused = False
            self._sync()

    # ---- 按钮入口 ----
    def _on_start(self) -> None:
        if not self.name_ed.text().strip():
            self.name_ed.setPlaceholderText("请先填写宏名称")
            return
        self._start(self.name_ed.text().strip())

    def _on_stop(self) -> None:
        self._stop()

    def _describe(self, s: dict) -> str:
        t = s.get("type", "?")
        if t == "cmd":
            return f"命令: {s.get('command', '')}"
        if t == "delay":
            return f"延时: {s.get('ms', 0)}"
        if t == "label":
            return f"标签: {s.get('label', '')}"
        if t == "jump":
            cond = s.get("condition") or {}
            ctype = {"contains": "包含", "regex": "正则", "cmp": "变量", "uncond": "无条件",
                     "jump": "条件"}.get(cond.get("type", "uncond"), "无条件")
            return f"跳转({ctype}) → {s.get('then', '')}"
        if t == "if":
            conds = s.get("conditions") or []
            then_t = s.get("then") or {}
            else_t = s.get("else") or {}
            tgt = then_t.get("target") if isinstance(then_t, dict) else then_t
            etg = else_t.get("target") if isinstance(else_t, dict) else else_t
            return f"判断({len(conds)}条件) 真→{tgt} 假→{etg}"
        if t == "status":
            then_t = s.get("then") or {}
            else_t = s.get("else") or {}
            tgt = then_t.get("target") if isinstance(then_t, dict) else then_t
            etg = else_t.get("target") if isinstance(else_t, dict) else else_t
            return f"状态({s.get('attr','qi')} {s.get('op','=')} {s.get('value','')}) 是→{tgt} 否→{etg}"
        if t == "input":
            return f"等待输入: {s.get('var', '')}"
        if t == "trigger":
            conds = s.get("conditions") or []
            if conds:
                base = f"触发: {conds[0].get('pattern', '')}"
            else:
                base = f"触发: {s.get('pattern', '')}"
            return base + self._onhit_suffix(s.get("on_hit"))
        if t == "cruise":
            conds = s.get("conditions") or []
            pat = conds[0].get("pattern", "") if conds else s.get("pattern", "")
            mode = "顺序" if s.get("mode", "ordered") == "ordered" else "随机"
            hm = {"home_exec": "返回起点执行", "exec_home": "执行后返回",
                  "exec": "仅执行", "home": "仅返回"}.get(s.get("hit_mode"), "")
            rh = f"·{hm}" if hm else ""
            return f"巡航[{mode}]: {s.get('range', '')} → {pat}{rh}"
        if t == "move_trigger":
            conds = s.get("conditions") or []
            pat = conds[0].get("pattern", "") if conds else s.get("pattern", "")
            return f"移动并触发: {s.get('command', '')} → {pat}" + self._onhit_suffix(s.get("on_hit"))
        if t == "captcha":
            return f"验证码: {s.get('command', '')} → ${s.get('var', 'captcha')}"
        if t == "hit":
            conds = s.get("conditions") or []
            pat = conds[0].get("pattern", "") if conds else s.get("pattern", "")
            return f"等待命中: {s.get('command', '')} → {pat}" + self._onhit_suffix(s.get("on_hit"))
        return t

    def _onhit_suffix(self, on_hit: dict | None) -> str:
        on_hit = on_hit or {}
        t = on_hit.get("type")
        if t == "cmd":
            return f" ·命中→{on_hit.get('command', '')}"
        if t == "jump":
            return f" ·命中→跳{on_hit.get('target', '')}"
        if t == "set":
            return f" ·命中→{on_hit.get('var', '')}={on_hit.get('value', '')}"
        return ""

    def _refresh(self) -> None:
        self.step_list.clear()
        for i, s in enumerate(self._steps):
            self.step_list.addItem(f"{i + 1}. {self._describe(s)}")


class AutomationDocks:
    """组合三个自动化 dock，供 MainWindow addDockWidget。"""

    def __init__(self, session) -> None:
        self.quick = QuickActionsDock(session)
        self.move = MoveControlDock(session)
        self.macro = MacroControlDock(session)