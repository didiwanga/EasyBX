from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QTransform
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
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
    """B9 快捷动作：按钮形式（标签只写作用），预设 + 用户自定义（本地配置）。"""

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setMinimumWidth(150)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(QLabel("快捷动作"))
        self.btn_grid = QGridLayout()
        self.btn_grid.setSpacing(2)
        lay.addLayout(self.btn_grid, 1)
        self.add_btn = QPushButton("+ 添加")
        self.add_btn.clicked.connect(self._on_add)
        lay.addWidget(self.add_btn)
        self._custom: dict[QPushButton, int] = {}   # 用户自定义按钮 → 配置索引
        self._rebuild()

    def bind(self, session) -> None:
        self.session = session
        self._rebuild()

    def _rebuild(self) -> None:
        while self.btn_grid.count():
            item = self.btn_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._custom_btns = {}
        custom = []
        if self.session is not None:
            raw = self.session.app.config.get("quick_actions")
            if isinstance(raw, list):
                custom = [r for r in raw if isinstance(r, list) and len(r) == 2]
        for i, (label, cmd) in enumerate(_QUICK_PRESETS):
            btn = QPushButton(label)
            btn.setToolTip(cmd)
            btn.clicked.connect(lambda _=False, c=cmd: self._fire(c))
            self.btn_grid.addWidget(btn, i // 2, i % 2)
        base = len(_QUICK_PRESETS)
        for j, row in enumerate(custom):
            btn = QPushButton(str(row[0]))
            btn.setToolTip(str(row[1]))
            btn.clicked.connect(lambda _=False, c=str(row[1]): self._fire(c))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda _pos, b=btn, idx=base + j: self._show_btn_menu(b, idx))
            self._custom_btns[btn] = base + j
            self.btn_grid.addWidget(btn, (base + j) // 2, (base + j) % 2)
        self.btn_grid.setColumnStretch(0, 1)
        self.btn_grid.setColumnStretch(1, 1)
        rows = (base + len(custom) + 1) // 2
        self.btn_grid.setRowStretch(rows + 1, 1)

    def _show_btn_menu(self, btn: QPushButton, idx: int) -> None:
        """右键菜单：删除用户自定义按钮。"""
        if self.session is None:
            return
        menu = QMenu(self)
        menu.addAction("删除该按钮", lambda: self._delete_btn(idx))
        menu.addSeparator()
        menu.addAction("取消")
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _delete_btn(self, idx: int) -> None:
        if self.session is None:
            return
        custom = [r for r in (self.session.app.config.get("quick_actions") or [])
                  if isinstance(r, list) and len(r) == 2]
        if 0 <= (base := idx - len(_QUICK_PRESETS)) < len(custom):
            custom.pop(base)
        self.session.app.config.set("quick_actions", custom)
        self._rebuild()

    def _fire(self, cmd: str) -> None:
        if self.session is not None:
            self.session.send(cmd)

    def _on_add(self) -> None:
        if self.session is None:
            return
        from PyQt6.QtWidgets import QDialog, QFormLayout, QLineEdit
        from PyQt6.QtWidgets import QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle("添加快捷动作")
        name_ed, cmd_ed = QLineEdit(), QLineEdit()
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
            custom = list(self.session.app.config.get("quick_actions") or [])
            custom.append([name_ed.text().strip(), cmd_ed.text().strip()])
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


_ARROW_CHAIN = {
    Qt.Key.Key_Up: ("n", "u", "d"),
    Qt.Key.Key_Down: ("s", "d", "u"),
    Qt.Key.Key_Left: ("w", "u", "d"),
    Qt.Key.Key_Right: ("e", "u", "d"),
}
_ARROW_ORDER = [Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right]


def arrow_move(exits: list[str], key) -> str | None:
    """智能回退：优先四向(n/s/e/w)，缺位补 u/d，再补其余可用；无出口返回 None。返回真实出口名。"""
    mapped = normalize_exits(exits)
    if not mapped:
        return None
    used: set[str] = set()
    assigned: dict = {}
    for k in _ARROW_ORDER:
        for cand in _ARROW_CHAIN[k]:
            if cand in mapped and cand not in used:
                assigned[k] = cand
                used.add(cand)
                break
    for k in _ARROW_ORDER:
        if k in assigned:
            continue
        hit = next((c for c in _ARROW_CHAIN[k] if c in mapped), None)
        assigned[k] = hit or next(iter(mapped))
    cand = assigned.get(key)
    return mapped.get(cand) if cand else None


class MoveControlDock(QWidget):
    """B9 移动控制：3×3 方向格 + up/down/enter/out，由 GMCP.Move 的 exits 驱动。"""

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setMinimumWidth(170)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(QLabel("移动 (GMCP)"))
        grid = QGridLayout()
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
                grid.addWidget(btn, r, c)
                if name != "look":
                    self._btns[name] = btn
        lay.addLayout(grid)
        row2 = QHBoxLayout()
        for name in _ROW2:
            if name in ("u", "d"):
                btn = QPushButton({"u": "上", "d": "下"}[name])
                btn.setProperty("dirBtn", True)
            else:
                btn = QPushButton({"enter": "进", "out": "出"}[name])
            btn.clicked.connect(lambda _=False, d=name: self._move(d))
            self._btns[name] = btn
            row2.addWidget(btn)
        lay.addLayout(row2)

        # 出口编号按钮：1-10，对应房间出口顺序（简写显示，点击发送完整出口名）
        self._num_btns: list[QPushButton] = []
        num_lay = FlowLayout(hspacing=4, vspacing=4)
        for i in range(1, 11):
            btn = QPushButton(str(i))
            btn.setProperty("numBtn", True)
            btn.setFixedSize(26, 26)
            btn.setToolTip(f"第 {i} 个出口")
            btn.clicked.connect(lambda _=False, idx=i - 1: self._move_num(idx))
            self._num_btns.append(btn)
            num_lay.addWidget(btn)
        lay.addLayout(num_lay)

        lay.addStretch(1)
        self._cur_exits: list[str] = []
        self._btn_exit: dict[str, str] = {}
        self.set_exits([])

    def bind(self, session) -> None:
        self.session = session
        self.set_exits(getattr(session, "exits", []) or [])

    def _move(self, d: str) -> None:
        if self.session is None:
            return
        if d in _EIGHT_DIRS:
            d = self._btn_exit.get(d, d)
        if d == "look":
            self.session.connection.send_line("look")
        else:
            self.session.send(d)

    def _move_num(self, idx: int) -> None:
        if self.session is None:
            return
        self.session.send(str(idx + 1))

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
        n = min(len(self._cur_exits), 10)
        for i, btn in enumerate(self._num_btns):
            btn.setEnabled(str(i + 1) in self._cur_exits)


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
                # 宏编辑器保存后实时刷新宏列表
                sub = bus.subscribe("automation.saved",
                                    lambda p: self._on_saved(p, acc))
                self._subs.append(("automation.saved", sub))
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
                    bus.unsubscribe(ev, sub)
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
        """按钮可用性：idle 仅运行；运行中 暂停+停止；暂停中 继续+停止。"""
        self.run_btn.setEnabled(idle)
        self.pause_btn.setEnabled(running and not paused)
        self.resume_btn.setEnabled(running and paused)
        self.stop_btn.setEnabled(running or not idle)

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
                return f"触发: {conds[0].get('pattern', '')}"
            return f"触发: {s.get('pattern', '')}"
        return t

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