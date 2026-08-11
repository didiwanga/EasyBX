from __future__ import annotations

import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

_ACTION_TYPES = [("cmd", "输出命令"), ("timer_start", "启动定时器"),
                 ("timer_stop", "停止定时器"), ("notify", "通知"), ("control", "控制")]

# 方向反转（B3b 5c 反转命令）：双线性反
_DIR_OPPOSITE = {
    "north": "south", "south": "north", "east": "west", "west": "east",
    "northeast": "southwest", "northwest": "southeast",
    "southeast": "northwest", "southwest": "northeast",
    "northup": "southdown", "northdown": "southup",
    "southup": "northdown", "southdown": "northup",
    "eastup": "westdown", "eastdown": "westup",
    "westup": "eastdown", "westdown": "eastup",
    "northeastup": "southwestdown", "northeastdown": "southwestup",
    "northwestup": "southeastdown", "northwestdown": "southeastup",
    "southeastup": "northwestdown", "southeastdown": "northwestup",
    "southwestup": "northeastdown", "southwestdown": "northeastup",
    "up": "down", "down": "up", "enter": "out", "out": "enter",
    "n": "s", "s": "n", "e": "w", "w": "e",
    "ne": "sw", "nw": "se", "se": "nw", "sw": "ne",
    "nu": "sd", "nd": "su", "su": "nd", "sd": "nu",
    "eu": "wd", "ed": "wu", "wu": "ed", "wd": "eu",
    "neu": "swd", "ned": "swu", "nwu": "sed", "nwd": "seu",
    "seu": "nwd", "sed": "nwu", "swu": "ned", "swd": "neu",
}


def _reverse_dir(word: str) -> str:
    """单个方向取反；`do N dir` 连续移动也取反方向（B3b 5c）。"""
    low = word.lower()
    if low in _DIR_OPPOSITE:
        return _DIR_OPPOSITE[low]
    # do 5 north / do 5n 连续移动
    m = re.match(r"^do\s+(\d+)\s+([a-z]+)$", low)
    if m:
        d = _DIR_OPPOSITE.get(m.group(2))
        if d:
            return f"do {m.group(1)} {d}"
    m = re.match(r"^do\s+(\d+)([a-z]+)$", low)
    if m:
        d = _DIR_OPPOSITE.get(m.group(2))
        if d:
            return f"do {m.group(1)}{d}"
    return word


def reverse_commands(text: str) -> str:
    """B3b 5c3：命令顺序倒序 + 每条移动方向取反。仅作用于编辑框内文本。"""
    parts = [p.strip() for p in text.split(";") if p.strip()]
    return ";".join(_reverse_dir(p) for p in reversed(parts))


class ActionEdit(QWidget):
    """动作列表编辑器：往返 dict 列表。"""

    def __init__(self, timer_names: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.timer_names = timer_names or []
        self.list = QListWidget()
        self.add_btn = QPushButton("＋ 动作")
        self.del_btn = QPushButton("－ 删除")
        self.add_btn.clicked.connect(self._on_add)
        self.del_btn.clicked.connect(self._on_del)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("动作"))
        lay.addWidget(self.list, 1)
        btns = QHBoxLayout()
        btns.addWidget(self.add_btn)
        btns.addWidget(self.del_btn)
        lay.addLayout(btns)
        self.actions: list[dict] = []

    def set_actions(self, actions: list[dict]) -> None:
        self.actions = [dict(a) for a in (actions or [])]
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        for a in self.actions:
            self.list.addItem(self._describe(a))

    def _describe(self, a: dict) -> str:
        code = a.get("type", "?")
        name = dict(_ACTION_TYPES).get(code, code)
        if code == "cmd":
            return f"{name}: {a.get('command', '')}"
        if code in ("timer_start", "timer_stop"):
            return f"{name}: {a.get('name', '')}"
        if code == "notify":
            return f"{name}: {a.get('message', '')}"
        if code == "control":
            return f"{name}: {a.get('target', '')}/{a.get('op', '')}"
        return name

    def _on_add(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("动作")
        type_cb = QComboBox()
        for code, label in _ACTION_TYPES:
            type_cb.addItem(label, code)
        cmd_ed = QLineEdit(); cmd_ed.setPlaceholderText("命令（{变量}）")
        timer_ed = QLineEdit()
        msg_ed = QLineEdit(); msg_ed.setPlaceholderText("通知内容")
        target_cb = QComboBox(); target_cb.addItems(["trigger", "macro", "timer"])
        op_cb = QComboBox(); op_cb.addItems(["start", "stop", "pause", "resume"])
        form = QFormLayout()
        form.addRow("类型", type_cb)
        form.addRow("命令", cmd_ed)
        form.addRow("定时器名", timer_ed)
        form.addRow("消息", msg_ed)
        form.addRow("控制", target_cb); form.addRow("操作", op_cb)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dlg)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg); lay.addLayout(form); lay.addWidget(box)
        if not dlg.exec():
            return
        t = type_cb.currentData()
        if t == "cmd":
            self.actions.append({"type": "cmd", "command": cmd_ed.text().strip()})
        elif t in ("timer_start", "timer_stop"):
            self.actions.append({"type": t, "name": timer_ed.text().strip()})
        elif t == "notify":
            self.actions.append({"type": "notify", "message": msg_ed.text().strip()})
        else:
            self.actions.append({"type": "control", "target": target_cb.currentText(), "op": op_cb.currentText()})
        self._refresh()

    def _on_del(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.actions.pop(row)
            self._refresh()


_MATCH_LABELS = [("contains", "包含"), ("regex", "正则"), ("exact", "精确"), ("template", "模板")]

# 状态步骤（B3b 新增）：GMCP 已获取的状态属性下拉选项（attr_key, 显示名）
_STATUS_ATTRS = [
    ("qi", "气血 qi"), ("max_qi", "气血上限 max_qi"),
    ("jing", "精神 jing"), ("max_jing", "精神上限 max_jing"),
    ("jingli", "精力 jingli"), ("max_jingli", "精力上限 max_jingli"),
    ("neili", "内力 neili"), ("max_neili", "内力上限 max_neili"),
    ("eff_qi", "有效气血 eff_qi"), ("eff_jing", "有效精神 eff_jing"),
    ("food", "食物 food"), ("water", "饮水 water"),
    ("level", "等级 level"), ("combat_exp", "经验 combat_exp"),
    ("potential", "潜能 potential"),
    ("fighter_spirit", "战意 fighter_spirit"), ("vigour", "真气 vigour"),
    ("yuan", "真元 yuan"),
]

# 状态比较运算符（op, 显示）
_STATUS_OPS = [("=", "="), ("!=", "≠"), (">", "＞"), ("<", "＜"), (">=", "≥"), ("<=", "≤")]

class ConditionListEdit(QWidget):
    """B3 多条件编辑器：条件列表（类型+模式/变量比较/状态比较）+ 与/或 关系。

    供触发器面板、宏「触发器步骤」、宏「判断步骤」复用同一套条件编辑 UI。
    条件数据格式：{match_type, pattern, var, op, value, attr}
    状态比较（match_type=status）：GMCP 状态属性(气血/内力等) 与 比较值 判断，
    类似宏「状态」步骤；引擎在匹配时用 session.state 取值比较。
    """

    def __init__(self, allow_cmp: bool = False, allow_status: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._allow_cmp = allow_cmp
        self._allow_status = allow_status
        self.conditions: list[dict] = []

        self.rel_cb = QComboBox()
        self.rel_cb.addItem("或 (任一命中)", "or")
        self.rel_cb.addItem("与 (全部命中)", "and")
        rel_row = QHBoxLayout()
        rel_row.addWidget(QLabel("关系"))
        rel_row.addWidget(self.rel_cb, 1)

        self.list = QListWidget()
        self.list.setMaximumHeight(96)
        self.add_btn = QPushButton("＋条件")
        self.del_btn = QPushButton("－条件")
        self.edit_btn = QPushButton("编辑")
        self.add_btn.clicked.connect(self._on_add)
        self.del_btn.clicked.connect(self._on_del)
        self.edit_btn.clicked.connect(self._on_edit)
        self.list.itemDoubleClicked.connect(lambda _i: self._on_edit())
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.edit_btn)
        btn_row.addWidget(self.del_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(rel_row)
        lay.addWidget(self.list)
        lay.addLayout(btn_row)

    def relation(self) -> str:
        return self.rel_cb.currentData() or "or"

    def set_relation(self, rel: str) -> None:
        idx = self.rel_cb.findData(rel)
        self.rel_cb.setCurrentIndex(max(0, idx))

    def set_conditions(self, conds: list) -> None:
        self.conditions = [dict(c) for c in (conds or [])]
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        for c in self.conditions:
            self.list.addItem(QListWidgetItem(self._desc(c)))

    def _desc(self, c: dict) -> str:
        mt = c.get("match_type", "contains")
        label = dict(_MATCH_LABELS).get(mt, mt)
        if mt == "cmp":
            return f"变量: {c.get('var', '')} {c.get('op', '=')} {c.get('value', '')}"
        if mt == "status":
            return f"状态: {c.get('attr', 'qi')} {c.get('op', '=')} {c.get('value', '')}"
        return f"{label}: {c.get('pattern', '')}"

    def _dlg(self, c: dict | None = None) -> dict | None:
        dlg = QDialog(self)
        dlg.setWindowTitle("条件")
        type_cb = QComboBox()
        for code, lab in _MATCH_LABELS:
            type_cb.addItem(lab, code)
        if self._allow_cmp:
            type_cb.addItem("变量比较", "cmp")
        if self._allow_status:
            type_cb.addItem("状态比较", "status")
        pat_ed = QLineEdit()
        pat_ed.setPlaceholderText("包含/正则/精确/模板文本，模板可含 {变量}")
        var_ed = QLineEdit()
        var_ed.setPlaceholderText("变量名，如 {v01} 或 地点")
        attr_cb = QComboBox()
        for key, lab in _STATUS_ATTRS:
            attr_cb.addItem(lab, key)
        op_cb = QComboBox()
        for op, lab in _STATUS_OPS:
            op_cb.addItem(lab, op)
        val_ed = QLineEdit()
        val_ed.setPlaceholderText("比较值（可含 {变量}）")

        form = QFormLayout()
        form.addRow("类型", type_cb)
        form.addRow("模式", pat_ed)
        form.addRow("变量", var_ed)
        form.addRow("状态属性", attr_cb)
        form.addRow("运算符", op_cb)
        form.addRow("比较值", val_ed)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dlg)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addLayout(form)
        lay.addWidget(box)

        def sync(idx: int) -> None:
            mt = type_cb.itemData(idx)
            is_cmp = mt == "cmp"
            is_st = mt == "status"
            pat_ed.setVisible(not is_cmp and not is_st)
            var_ed.setVisible(is_cmp)
            attr_cb.setVisible(is_st)
            op_cb.setVisible(is_cmp or is_st)
            val_ed.setVisible(is_cmp or is_st)

        type_cb.currentIndexChanged.connect(sync)
        if c:
            mt = c.get("match_type", "contains")
            type_cb.setCurrentIndex(max(0, type_cb.findData(mt)))
            pat_ed.setText(c.get("pattern", ""))
            var_ed.setText(c.get("var", ""))
            attr_cb.setCurrentIndex(max(0, attr_cb.findData(c.get("attr", "qi"))))
            op_cb.setCurrentIndex(max(0, op_cb.findData(c.get("op", "="))))
            val_ed.setText(c.get("value", ""))
        sync(type_cb.currentIndex())
        if not dlg.exec():
            return None
        mt = type_cb.currentData()
        if mt == "cmp":
            return {"match_type": "cmp", "var": var_ed.text().strip() or "{v01}",
                    "op": op_cb.currentData() or "=", "value": val_ed.text().strip()}
        if mt == "status":
            return {"match_type": "status", "attr": attr_cb.currentData() or "qi",
                    "op": op_cb.currentData() or "=", "value": val_ed.text().strip()}
        return {"match_type": mt, "pattern": pat_ed.text().strip()}

    def _on_add(self) -> None:
        c = self._dlg()
        if c:
            self.conditions.append(c)
            self._refresh()

    def _on_edit(self) -> None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self.conditions):
            return
        c = self._dlg(self.conditions[row])
        if c:
            self.conditions[row] = c
            self._refresh()

    def _on_del(self) -> None:
        row = self.list.currentRow()
        if row >= 0 and row < len(self.conditions):
            self.conditions.pop(row)
            self._refresh()

class _EditorBase(QDialog):
    _key = "aliases"
    _show_left_save = True   # 左侧「保存」按钮（删除后）；宏用步骤区保存宏，设 False

    def __init__(self, title: str, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle(title)
        self.resize(860, 540)

        self.item_list = QTreeWidget()
        self.item_list.setHeaderHidden(True)
        self.item_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.item_list.currentItemChanged.connect(self._on_select)
        self.item_list.customContextMenuRequested.connect(self._list_menu)
        self.new_btn = QPushButton("新建")
        self.del_btn = QPushButton("删除")
        self.save_btn = QPushButton("保存")
        self.save_btn.setToolTip("保存当前项到列表并落盘（不关闭窗口），可继续编辑")
        self.save_btn.clicked.connect(self._on_save_no_close)
        if not self._show_left_save:
            self.save_btn.hide()
        self.new_btn.clicked.connect(self._on_new)
        self.del_btn.clicked.connect(self._on_delete)

        self.form = QFormLayout()
        self.name_ed = QLineEdit()
        self.group_ed = QComboBox()
        self.group_ed.setEditable(True)
        self.group_ed.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.group_ed.lineEdit().setPlaceholderText("选择已有分组，或输入新分组名")
        self.enabled_cb = QCheckBox("启用")
        self.shared_cb = QCheckBox("共享到所有账号")
        self.form.addRow("名称", self.name_ed)
        self.form.addRow("分组", self.group_ed)
        self.form.addRow(self.enabled_cb)
        self.form.addRow(self.shared_cb)
        self._build_form()

        right = QVBoxLayout()
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        box.rejected.connect(self.close)
        right.addLayout(self.form)
        right.addWidget(box)

        left = QVBoxLayout()
        left.addWidget(QLabel(f"{title}列表"))
        left.addWidget(self.item_list, 1)
        left.addWidget(self.new_btn)
        left.addWidget(self.del_btn)
        left.addWidget(self.save_btn)

        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addLayout(right, 2)

        self.items: list[dict] = []
        self._edit_idx: int | None = None   # 当前正在编辑的具体项索引（Save 用）
        self._load_items()

    # ---- 子类接口 ----
    def _build_form(self) -> None:
        pass

    def _extra(self, d: dict) -> dict:
        return {}

    def _apply_form(self, d: dict) -> dict:
        d.update(self._extra(d))
        return d

    def _to_label(self, d: dict) -> str:
        return d.get("name", "?")

    def _reload_engine(self) -> None:
        pass

    # ---- 装载 ----
    def _load_items(self) -> None:
        cfg = self.session.app.config
        data = cfg.automation(self.session.account_id)
        self.items = [dict(i) for i in data.get(self._key, [])]
        self._refresh()

    _UNGROUPED = "（未分组）"

    def _refresh(self) -> None:
        """按分组构建树：顶层=分组，子项=具体项；折叠分组，右键可批量启停。"""
        self.item_list.clear()
        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._ungrouped_item: QTreeWidgetItem | None = None
        grouped = any((d.get("group") or "") for d in self.items)
        if grouped:
            self._group_items[self._UNGROUPED] = self._new_group_node(self._UNGROUPED)
        for idx, d in enumerate(self.items):
            grp = (d.get("group") or "").strip() or self._UNGROUPED
            node = self._group_items.get(grp)
            if node is None:
                node = self._new_group_node(grp)
                self._group_items[grp] = node
            child = QTreeWidgetItem([d.get("name", "?")])
            if not bool(d.get("enabled", True)):
                child.setForeground(0, Qt.GlobalColor.gray)
            child.setData(0, Qt.ItemDataRole.UserRole, idx)
            node.addChild(child)
        self.item_list.expandAll()
        self._refresh_groups()

    def _refresh_groups(self) -> None:
        """分组下拉：列出已有分组名（去重），不覆盖当前已选/输入值。"""
        cur = self.group_ed.currentText()
        names = sorted({(d.get("group") or "").strip() for d in self.items if (d.get("group") or "").strip()})
        self.group_ed.blockSignals(True)
        self.group_ed.clear()
        self.group_ed.addItem("")   # 空 = 未分组
        self.group_ed.addItems(names)
        self.group_ed.blockSignals(False)
        if cur:
            idx = self.group_ed.findText(cur)
            self.group_ed.setCurrentIndex(idx if idx >= 0 else 0)
            if idx < 0:
                # 输入的是新分组名：设为当前编辑文本
                self.group_ed.setEditText(cur)

    def _new_group_node(self, title: str) -> QTreeWidgetItem:
        node = QTreeWidgetItem([title])
        node.setData(0, Qt.ItemDataRole.UserRole + 1, True)  # 标记为分组节点
        node.setFlags(node.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.item_list.addTopLevelItem(node)
        return node

    def _list_menu(self, pos) -> None:
        item = self.item_list.itemAt(pos)
        if item is None:
            return
        is_group = item.data(0, Qt.ItemDataRole.UserRole + 1)
        menu = QMenu(self)
        if is_group:
            grp = item.text(0)
            if grp == self._UNGROUPED:
                grp = ""
            menu.addAction("启用整组", lambda g=grp: self._group_toggle(g, True))
            menu.addAction("停用整组", lambda g=grp: self._group_toggle(g, False))
            menu.addAction("展开/折叠", item.setExpanded)
        else:
            idx = item.data(0, Qt.ItemDataRole.UserRole)
            if idx is not None and 0 <= idx < len(self.items):
                menu.addAction("启用", lambda i=idx: self._single_toggle(i, True))
                menu.addAction("停用", lambda i=idx: self._single_toggle(i, False))
        menu.exec(self.item_list.viewport().mapToGlobal(pos))

    def _group_toggle(self, group: str, on: bool) -> None:
        for d in self.items:
            if (d.get("group") or "").strip() == group:
                d["enabled"] = on
        self._refresh()
        self._persist()

    def _single_toggle(self, idx: int, on: bool) -> None:
        if 0 <= idx < len(self.items):
            self.items[idx]["enabled"] = on
        self._refresh()
        self._persist()

    def _on_select(self, cur, _prev) -> None:
        if cur is None:
            return
        if cur.data(0, Qt.ItemDataRole.UserRole + 1):
            return
        idx = cur.data(0, Qt.ItemDataRole.UserRole)
        if idx is None or not (0 <= idx < len(self.items)):
            return
        self._edit_idx = idx
        self._fill_form(self.items[idx])

    def _fill_form(self, item: dict) -> None:
        self.name_ed.setText(item.get("name", ""))
        grp = item.get("group", "")
        if grp:
            idx = self.group_ed.findText(grp)
            if idx >= 0:
                self.group_ed.setCurrentIndex(idx)
            else:
                self.group_ed.setEditText(grp)
        else:
            self.group_ed.setCurrentIndex(0)
        self.enabled_cb.setChecked(bool(item.get("enabled", True)))
        self.shared_cb.setChecked(bool(item.get("shared", False)))
        self._fill_extra(item)

    def _fill_extra(self, item: dict) -> None:
        pass

    def _on_new(self) -> None:
        # 名称留空由用户填写；分组自动跟随列表当前选中项（分组夹/分组内宏）
        item = {"name": "", "enabled": True, "shared": False}
        item["group"] = self._selected_group()
        self.items.append(item)
        self._refresh()
        self._select_index(len(self.items) - 1)

    def _selected_group(self) -> str:
        """新建时分组自动跟随列表选中项：分组夹→该分组；分组内宏→该宏分组；否则留空。"""
        cur = self.item_list.currentItem()
        if cur is None:
            return ""
        if cur.data(0, Qt.ItemDataRole.UserRole + 1):
            # 分组夹：取分组名（未分组夹留空）
            g = cur.text(0)
            return "" if g == self._UNGROUPED else g
        # 分组内的宏：沿用其分组
        idx = cur.data(0, Qt.ItemDataRole.UserRole)
        if idx is not None and 0 <= idx < len(self.items):
            return (self.items[idx].get("group") or "").strip()
        return ""

    def _select_index(self, idx: int) -> None:
        def walk(node: QTreeWidgetItem):
            for i in range(node.childCount()):
                child = node.child(i)
                if child.data(0, Qt.ItemDataRole.UserRole) == idx:
                    self.item_list.setCurrentItem(child)
                    return True
            return False
        for i in range(self.item_list.topLevelItemCount()):
            if walk(self.item_list.topLevelItem(i)):
                return

    def _on_delete(self) -> None:
        item = self.item_list.currentItem()
        if item is None or item.data(0, Qt.ItemDataRole.UserRole + 1):
            return
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        if idx is not None and 0 <= idx < len(self.items):
            self.items.pop(idx)
            self._edit_idx = None
            self._refresh()
            self._persist()   # 删除即时落盘，避免重开后复活

    def _on_save_no_close(self) -> None:
        """「保存」按钮：保存当前项到列表并落盘，不关闭窗口（可继续编辑其他项）。"""
        idx = self._edit_idx
        item = self.item_list.currentItem()
        if item is not None and not item.data(0, Qt.ItemDataRole.UserRole + 1):
            ci = item.data(0, Qt.ItemDataRole.UserRole)
            if ci is not None and 0 <= ci < len(self.items):
                idx = ci
        if idx is None or not (0 <= idx < len(self.items)):
            self._on_new()   # 无当前项：新建一个再保存
        self._persist_current()
        self._refresh()
        self.session.app.bus.publish("ui.message", account=self.session.account_id,
                                     message=f"{self._key} 已保存（可继续编辑）")

    def _persist_current(self) -> None:
        """把当前表单（名称/分组/启用/共享 + 子字段）写回 items 并落盘，不关闭窗口。

        优先用当前选中项；选中分组节点/无选中时用最近编辑项，避免表单改动丢失。
        """
        idx = self._edit_idx
        item = self.item_list.currentItem()
        if item is not None and not item.data(0, Qt.ItemDataRole.UserRole + 1):
            ci = item.data(0, Qt.ItemDataRole.UserRole)
            if ci is not None and 0 <= ci < len(self.items):
                idx = ci
        if idx is not None and 0 <= idx < len(self.items):
            # 名称留空时保留现有名/默认名，保证步骤等子字段仍被写入
            cur_name = self.items[idx].get("name", "")
            name = self.name_ed.text().strip() or cur_name or f"{self._key}_new"
            d = {"name": name,
                 "group": self.group_ed.currentText().strip(),
                 "enabled": self.enabled_cb.isChecked(),
                 "shared": self.shared_cb.isChecked()}
            self.items[idx] = self._apply_form(d)
            if not self.name_ed.text().strip():
                self.name_ed.setText(name)
        self._persist()

    def closeEvent(self, event) -> None:
        # 关窗即保存：先把表单写回 items，再丢弃未命名且无内容的纯空白新建项后落盘
        self._persist_current()
        self.items = [d for d in self.items if not self._is_blank(d)]
        self._persist()
        super().closeEvent(event)

    def _is_blank(self, d: dict) -> bool:
        """纯空白新建项：未填写名称（空或默认占位名）且无有效内容，关窗时丢弃。"""
        name = (d.get("name") or "").strip()
        default = f"{self._key}_new"
        if name not in ("", default):
            return False
        if self._key == "macros":
            return not d.get("steps")
        if self._key == "aliases":
            return not (d.get("pattern") or "").strip() and not (d.get("replacement") or "").strip()
        if self._key == "timers":
            sched = d.get("schedule") or {}
            has_time = bool(sched.get("daily_at") or sched.get("once_at") or sched.get("week_days"))
            return not has_time and not d.get("actions")
        # 触发器/通用：主 pattern 或附加条件含有效模式，或动作非空
        if (d.get("pattern") or "").strip():
            return False
        for c in (d.get("conditions") or []):
            if (c.get("pattern") or "").strip():
                return False
        return not d.get("actions")

    def _persist(self) -> None:
        """写入配置并重载引擎（含右键批量启停时调用，此时不关闭对话框）。

        E8 作用域：shared=True 项整体写全局文件，其余写账号文件。
        整体替换（而非合并），保证删除项真正从文件移除、重开不复活。
        """
        cfg = self.session.app.config
        shared_items = [d for d in self.items if d.get("shared")]
        own_items = [d for d in self.items if not d.get("shared")]
        cfg.save_automation(None, self._key, shared_items)
        cfg.save_automation(self.session.account_id, self._key, own_items)
        self._reload_engine()
        # 通知宏控制/录制等 dock 实时刷新列表
        self.session.app.bus.publish("automation.saved", account=self.session.account_id,
                                     kind=self._key)

    def _timers(self) -> list[str]:
        return list(self.session.timers.list())


class TriggerEditor(_EditorBase):
    _key = "triggers"

    def __init__(self, session, parent=None) -> None:
        super().__init__("触发器", session, parent)
        self.setWindowTitle("触发器编辑器")

    def _build_form(self) -> None:
        self.match_cb = QComboBox()
        for code, lab in _MATCH_LABELS:
            self.match_cb.addItem(lab, code)
        self.pattern_ed = QLineEdit()
        self.delay_sb = QSpinBox(); self.delay_sb.setRange(0, 3600000); self.delay_sb.setSuffix(" ms")
        self.oneshot_cb = QCheckBox("仅执行一次")
        self.form.addRow("匹配类型", self.match_cb)
        self.form.addRow("模式", self.pattern_ed)
        self.form.addRow("延时", self.delay_sb)
        self.form.addRow(self.oneshot_cb)

        # B3 多条件：与/或 关系 + 条件列表（条件编辑器组件，模板可含 {变量} 捕获）
        self.cond_edit = ConditionListEdit(allow_cmp=False, allow_status=True)
        self.cond_edit.setMinimumHeight(170)
        self.form.addRow("条件列表", self.cond_edit)

        # B3 计数器
        self.cn_label = QLabel("命中 0 次")
        self.reset_cnt_btn = QPushButton("重置计数")
        self.reset_cnt_btn.clicked.connect(self._reset_counter)
        cnt_row = QHBoxLayout(); cnt_row.addWidget(self.cn_label); cnt_row.addWidget(self.reset_cnt_btn)
        self.form.addRow("计数", cnt_row)

        self.actions = ActionEdit(self._timers())
        self.form.addRow(self.actions)

    def _fill_extra(self, item: dict) -> None:
        mt = item.get("match_type", "contains")
        self.match_cb.setCurrentIndex(max(0, self.match_cb.findData(mt)))
        self.pattern_ed.setText(item.get("pattern", ""))
        self.delay_sb.setValue(int(item.get("delay_ms", 0)))
        self.oneshot_cb.setChecked(bool(item.get("one_shot", False)))
        self.actions.set_actions(item.get("actions", []))
        # 条件集 = 主条件(顶层 pattern) + 附加条件；兼容旧数据（conditions 非空但主条件独立）
        conds = [dict(c) for c in (item.get("conditions") or [])]
        main = {"match_type": mt, "pattern": item.get("pattern", "")}
        if main["pattern"]:
            if conds and conds[0].get("match_type") == mt and conds[0].get("pattern") == main["pattern"]:
                pass  # 首条件即主条件，避免重复
            else:
                conds.insert(0, main)
        self.cond_edit.set_conditions(conds)
        self.cond_edit.set_relation(item.get("relation", "or"))
        self._refresh_counter()

    def _extra(self, d: dict) -> dict:
        mt = self.match_cb.currentData()
        conds = list(self.cond_edit.conditions)
        if not conds:
            conds = [{"match_type": mt, "pattern": self.pattern_ed.text()}]
        # 主条件与顶层 pattern/匹配类型保持一致（引擎旧路径读顶层）
        return {"match_type": conds[0].get("match_type", mt),
                "pattern": conds[0].get("pattern", ""),
                "delay_ms": self.delay_sb.value(), "one_shot": self.oneshot_cb.isChecked(),
                "actions": list(self.actions.actions),
                "conditions": conds,
                "relation": self.cond_edit.relation(),
                "counter": self._counter()}

    def _counter(self) -> int:
        try:
            return self.session.triggers.count(self._cur_name())
        except Exception:
            return 0

    def _cur_name(self) -> str:
        cur = self.item_list.currentItem()
        if cur is not None and not cur.data(0, Qt.ItemDataRole.UserRole + 1):
            idx = cur.data(0, Qt.ItemDataRole.UserRole)
            if idx is not None and 0 <= idx < len(self.items):
                return self.items[idx].get("name", "")
        return self.name_ed.text().strip()

    def _refresh_counter(self) -> None:
        try:
            self.cn_label.setText(f"命中 {self._counter()} 次")
        except Exception:
            self.cn_label.setText("命中 0 次")

    def _reset_counter(self) -> None:
        try:
            self.session.triggers.reset_counter(self._cur_name())
        except Exception:
            pass
        self._refresh_counter()

    def _reload_engine(self) -> None:
        cfg = self.session.app.config
        self.session.triggers.load(cfg.automation(self.session.account_id)["triggers"])


class AliasEditor(_EditorBase):
    _key = "aliases"

    def __init__(self, session, parent=None) -> None:
        super().__init__("别名", session, parent)

    def _build_form(self) -> None:
        self.pattern_ed = QLineEdit(); self.pattern_ed.setPlaceholderText("前缀/正则")
        self.replacement_ed = QLineEdit(); self.replacement_ed.setPlaceholderText("展开命令（%1 捕获）")
        self.form.addRow("命令前缀", self.pattern_ed)
        self.form.addRow("展开为", self.replacement_ed)

    def _fill_extra(self, item: dict) -> None:
        self.pattern_ed.setText(item.get("pattern", ""))
        self.replacement_ed.setText(item.get("replacement", ""))

    def _extra(self, d: dict) -> dict:
        return {"pattern": self.pattern_ed.text(), "replacement": self.replacement_ed.text()}

    def _reload_engine(self) -> None:
        cfg = self.session.app.config
        self.session.aliases.load(cfg.automation(self.session.account_id)["aliases"])


class TimerEditor(_EditorBase):
    _key = "timers"

    def __init__(self, session, parent=None) -> None:
        super().__init__("定时器", session, parent)

    def _build_form(self) -> None:
        self.sched_cb = QComboBox(); self.sched_cb.addItems(["interval", "daily", "weekly", "once"])
        self.interval_sb = QSpinBox(); self.interval_sb.setRange(1, 86400000); self.interval_sb.setSuffix(" ms")
        self.time_ed = QLineEdit(); self.time_ed.setPlaceholderText("HH:MM")
        self.days_ed = QLineEdit(); self.days_ed.setPlaceholderText("星期 1-7，逗号分隔")
        self.once_ed = QLineEdit(); self.once_ed.setPlaceholderText("YYYY-MM-DD HH:MM")
        self.form.addRow("类型", self.sched_cb)
        self.form.addRow("间隔", self.interval_sb)
        self.form.addRow("每日时间", self.time_ed)
        self.form.addRow("星期", self.days_ed)
        self.form.addRow("一次性时间", self.once_ed)
        self.actions = ActionEdit()
        self.form.addRow(self.actions)

    def _fill_extra(self, item: dict) -> None:
        sched = item.get("schedule") or {}
        self.sched_cb.setCurrentText(sched.get("type", "interval"))
        self.interval_sb.setValue(int(sched.get("interval_ms", 1000)))
        times = sched.get("daily_at") or []
        self.time_ed.setText(times[0] if times else "")
        self.days_ed.setText(",".join(map(str, sched.get("week_days") or [])))
        self.once_ed.setText(sched.get("once_at", ""))
        self.actions.set_actions(item.get("actions", []))

    def _extra(self, d: dict) -> dict:
        ttype = self.sched_cb.currentText()
        times = [self.time_ed.text().strip()] if self.time_ed.text().strip() else []
        days = [int(x) for x in self.days_ed.text().replace("，", ",").split(",") if x.strip().isdigit()]
        sched = {"type": ttype}
        if ttype == "interval":
            sched["interval_ms"] = self.interval_sb.value()
        if times:
            sched["daily_at"] = times
        if days:
            sched["week_days"] = days
        if self.once_ed.text().strip():
            sched["once_at"] = self.once_ed.text().strip()
        return {"schedule": sched, "actions": list(self.actions.actions)}

    def _reload_engine(self) -> None:
        cfg = self.session.app.config
        self.session.timers.load(cfg.automation(self.session.account_id)["timers"])


class MacroEditor(_EditorBase):
    _key = "macros"
    _show_left_save = False   # 宏用步骤区「保存宏」按钮，隐藏左侧保存

    def __init__(self, session, parent=None) -> None:
        super().__init__("宏", session, parent)

    def _build_form(self) -> None:
        self.step_list = QListWidget()
        self.step_save = QPushButton("保存宏")
        self.step_save.setToolTip("把当前正在编辑的宏保存到列表（不关闭窗口），可继续编辑")
        self.step_add = QPushButton("＋步骤")
        self.step_edit = QPushButton("编辑")
        self.step_del = QPushButton("－删除")
        self.step_up = QPushButton("↑上移")
        self.step_dn = QPushButton("↓下移")
        self.step_save.clicked.connect(self._on_step_save)
        self.step_add.clicked.connect(self._on_step_add)
        self.step_edit.clicked.connect(self._on_step_edit)
        self.step_del.clicked.connect(self._on_step_del)
        self.step_up.clicked.connect(self._on_step_up)
        self.step_dn.clicked.connect(self._on_step_dn)
        self.step_list.itemDoubleClicked.connect(lambda _i: self._on_step_edit())
        self.form.addRow("步骤", self.step_list)
        btn1 = QHBoxLayout(); btn1.addWidget(self.step_add)
        btn1.addWidget(self.step_edit); btn1.addWidget(self.step_del)
        btn1.addWidget(self.step_save)
        btn2 = QHBoxLayout(); btn2.addWidget(self.step_up); btn2.addWidget(self.step_dn)
        self.form.addRow(btn1)
        self.form.addRow(btn2)
        self._steps: list[dict] = []

    def _fill_extra(self, item: dict) -> None:
        self._steps = [dict(s) for s in item.get("steps", [])]
        self._refresh_steps()

    def _refresh_steps(self) -> None:
        self.step_list.clear()
        for s in self._steps:
            self.step_list.addItem(self._step_desc(s))

    def _step_desc(self, s: dict) -> str:
        t = s.get("type")
        if t == "cmd":
            return f"命令: {s.get('command', '')}"
        if t == "delay":
            return f"延时: {s.get('ms', 0)}ms"
        if t == "label":
            return f"标签: {s.get('label', '')}"
        if t == "jump":
            cond = s.get("condition") or {}
            ctype = {"contains": "包含", "regex": "正则", "cmp": "变量", "uncond": "无条件",
                     "jump": "条件"}.get(cond.get("type", "uncond"), "无条件")
            return f"跳转({ctype}) → {s.get('then', '')}"
        if t == "if":
            conds = s.get("conditions") or []
            n = len(conds)
            rel = {"and": "且", "or": "或"}.get(s.get("relation", "or"), "或")
            then_t = s.get("then") or {}
            else_t = s.get("else") or {}
            tgt = then_t.get("target") if isinstance(then_t, dict) else then_t
            etg = else_t.get("target") if isinstance(else_t, dict) else else_t
            return f"判断({n}条件·{rel}) 真→{tgt} 假→{etg}"
        if t == "status":
            attr = s.get("attr", "qi")
            op = s.get("op", "=")
            val = s.get("value", "")
            then_t = s.get("then") or {}
            else_t = s.get("else") or {}
            tgt = then_t.get("target") if isinstance(then_t, dict) else then_t
            etg = else_t.get("target") if isinstance(else_t, dict) else else_t
            return f"状态({attr} {op} {val}) 是→{tgt} 否→{etg}"
        if t == "input":
            return f"等待输入: 变量={s.get('var', '')}"
        if t == "trigger":
            conds = s.get("conditions") or []
            if conds:
                c = conds[0]
                return f"触发: {c.get('match_type', 'contains')} {c.get('pattern', '')}"
            cond = s.get("condition") or {}
            pat = s.get("pattern") or cond.get("pattern", "")
            return f"触发: {cond.get('type', s.get('match_type', 'contains'))} {pat}"
        if t == "captcha":
            return f"验证码: {s.get('command', '')} → ${s.get('var', 'captcha')}"
        return f"{t}"

    def _on_step_save(self) -> None:
        """保存当前宏到列表并落盘，不关闭窗口（可继续编辑其他项）。"""
        idx = self._edit_idx
        item = self.item_list.currentItem()
        if item is not None and not item.data(0, Qt.ItemDataRole.UserRole + 1):
            ci = item.data(0, Qt.ItemDataRole.UserRole)
            if ci is not None and 0 <= ci < len(self.items):
                idx = ci
        if idx is None or not (0 <= idx < len(self.items)):
            # 无当前项：追加新宏（不触发选中/填充，避免覆盖 self._steps）
            name = self.name_ed.text().strip() or f"{self._key}_new"
            self.items.append({"name": name, "enabled": True, "shared": False,
                               "group": self._selected_group()})
            self._edit_idx = len(self.items) - 1
        self._persist_current()
        self._refresh()
        self.session.app.bus.publish("ui.message", account=self.session.account_id,
                                     message=f"宏已保存（可继续编辑）")

    def _on_step_add(self) -> None:
        dlg = StepDialog(self._steps, None, self)
        if dlg.exec() and dlg.result_step():
            self._steps.append(dlg.result_step())
            self._refresh_steps()
            self.step_list.setCurrentRow(len(self._steps) - 1)

    def _on_step_edit(self) -> None:
        row = self.step_list.currentRow()
        if row < 0 or row >= len(self._steps):
            return
        dlg = StepDialog(self._steps, self._steps[row], self)
        if dlg.exec() and dlg.result_step():
            self._steps[row] = dlg.result_step()
            self._refresh_steps()
            self.step_list.setCurrentRow(row)

    def _on_step_del(self) -> None:
        row = self.step_list.currentRow()
        if row >= 0:
            self._steps.pop(row)
            self._refresh_steps()

    def _on_step_up(self) -> None:
        row = self.step_list.currentRow()
        if row > 0 and row < len(self._steps):
            self._steps[row], self._steps[row - 1] = self._steps[row - 1], self._steps[row]
            self._refresh_steps()
            self.step_list.setCurrentRow(row - 1)

    def _on_step_dn(self) -> None:
        row = self.step_list.currentRow()
        if 0 <= row < len(self._steps) - 1:
            self._steps[row], self._steps[row + 1] = self._steps[row + 1], self._steps[row]
            self._refresh_steps()
            self.step_list.setCurrentRow(row + 1)

    def _extra(self, d: dict) -> dict:
        return {"steps": [dict(s) for s in self._steps]}

    def _reload_engine(self) -> None:
        cfg = self.session.app.config
        self.session.macros.load(cfg.automation(self.session.account_id)["macros"])


class StepDialog(QDialog):
    """宏步骤编辑（B3b 七种步骤，按类型动态显示字段）。

    类型：cmd(命令) / delay(延时) / label(标签) / jump(跳转) / if(判断) /
          input(等待输入) / trigger(触发器步骤，复用 B3 条件表单)。
    跳转/判断目标仅从「已有标签 + 步骤序号」下拉选择（设计文档强制，避免运行找不到目标）。
    """

    _STEP_LABELS = [("cmd", "命令"), ("delay", "延时"), ("label", "标签"),
                    ("jump", "跳转"), ("if", "判断"), ("status", "状态"),
                    ("input", "等待输入"), ("trigger", "触发"), ("captcha", "验证码")]

    def __init__(self, steps: list | None = None, default: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("宏步骤")
        self._step: dict | None = None
        self._steps = steps or []

        # ---- 类型选择 ----
        self.type_cb = QComboBox()
        for code, lab in self._STEP_LABELS:
            self.type_cb.addItem(lab, code)
        self.type_cb.currentIndexChanged.connect(self._sync_ui)

        # ---- 命令页 ----
        self.cmd_ed = QLineEdit()
        self.cmd_rev = QPushButton("反转命令")
        self.cmd_rev.setToolTip("命令顺序倒序 + 方向取反，仅作用于本编辑框（B3b 5c）")
        self.cmd_rev.clicked.connect(self._reverse_cmd)
        cmd_row = QHBoxLayout()
        cmd_row.addWidget(self.cmd_ed, 1)
        cmd_row.addWidget(self.cmd_rev)

        # ---- 延时页 ----
        self.ms_sb = QSpinBox(); self.ms_sb.setRange(0, 86400000)
        self.ms_sb.setValue(500); self.ms_sb.setSuffix(" ms")

        # ---- 标签页 ----
        self.label_ed = QLineEdit(); self.label_ed.setPlaceholderText("标签名（供跳转/判断引用）")

        # ---- 跳转页：目标下拉 + 可选条件 ----
        self.jump_target_cb = QComboBox()
        self.jump_cond_type = QComboBox()
        for code, lab in [("none", "无条件"), ("contains", "包含文本"), ("regex", "匹配正则"),
                          ("cmp", "变量比较")]:
            self.jump_cond_type.addItem(lab, code)
        self.jump_cond_pat = QLineEdit()
        self.jump_cond_pat.setPlaceholderText("包含/正则文本，或 `{变量}`")
        self.jump_cond_op = QComboBox()
        for op, lab in [("=", "="), ("!=", "≠"), (">", "＞"), ("<", "＜")]:
            self.jump_cond_op.addItem(lab, op)
        self.jump_cond_val = QLineEdit(); self.jump_cond_val.setPlaceholderText("比较值")

        # ---- 判断页：条件列表 + 与/或 + 真/假分支 ----
        self.if_cond = ConditionListEdit(allow_cmp=True, allow_status=True)
        self.if_cond.setMinimumHeight(160)
        self.if_then_target = QComboBox()
        self.if_then_action_type = QComboBox()
        for code, lab in [("none", "无动作"), ("cmd", "发送命令"), ("set", "变量赋值")]:
            self.if_then_action_type.addItem(lab, code)
        self.if_then_action_ed = QLineEdit(); self.if_then_action_ed.setPlaceholderText("命令 或 {变量}=值")
        self.if_else_target = QComboBox()
        self.if_else_action_type = QComboBox()
        for code, lab in [("none", "无动作"), ("cmd", "发送命令"), ("set", "变量赋值")]:
            self.if_else_action_type.addItem(lab, code)
        self.if_else_action_ed = QLineEdit(); self.if_else_action_ed.setPlaceholderText("命令 或 {变量}=值")

        # ---- 状态页：GMCP 状态属性比较 + 真/假分支 ----
        self.status_attr = QComboBox()
        for key, lab in _STATUS_ATTRS:
            self.status_attr.addItem(lab, key)
        self.status_op = QComboBox()
        for op, lab in _STATUS_OPS:
            self.status_op.addItem(lab, op)
        self.status_val = QLineEdit()
        self.status_val.setPlaceholderText("比较值（可含 {变量}）")
        self.status_then_target = QComboBox()
        self.status_then_action_type = QComboBox()
        for code, lab in [("none", "无动作"), ("cmd", "发送命令"), ("set", "变量赋值")]:
            self.status_then_action_type.addItem(lab, code)
        self.status_then_action_ed = QLineEdit(); self.status_then_action_ed.setPlaceholderText("命令 或 {变量}=值")
        self.status_else_target = QComboBox()
        self.status_else_action_type = QComboBox()
        for code, lab in [("none", "无动作"), ("cmd", "发送命令"), ("set", "变量赋值")]:
            self.status_else_action_type.addItem(lab, code)
        self.status_else_action_ed = QLineEdit(); self.status_else_action_ed.setPlaceholderText("命令 或 {变量}=值")

        # ---- 等待输入页 ----
        self.var_ed = QLineEdit(); self.var_ed.setPlaceholderText("等待赋值的变量名，如 {v01}")
        self.prompt_ed = QLineEdit(); self.prompt_ed.setPlaceholderText("提示词（如 口令）")
        self.timeout_sb = QSpinBox(); self.timeout_sb.setRange(0, 3600000); self.timeout_sb.setSuffix(" ms")

        # ---- 触发器页：复用 B3 条件表单 ----
        self.trg_cond = ConditionListEdit(allow_cmp=False, allow_status=True)
        self.trg_cond.setMinimumHeight(160)
        self.trg_delay_sb = QSpinBox(); self.trg_delay_sb.setRange(0, 3600000)
        self.trg_delay_sb.setSuffix(" ms")
        self.trg_timeout_sb = QSpinBox(); self.trg_timeout_sb.setRange(0, 3600000)
        self.trg_timeout_sb.setSuffix(" ms")

        # ---- 验证码页 ----
        self.cap_cmd_ed = QLineEdit()
        self.cap_cmd_ed.setPlaceholderText("如 fullme（发送的命令）")
        self.cap_var_ed = QLineEdit()
        self.cap_var_ed.setPlaceholderText("接收验证码的变量名，如 code")
        self.cap_timeout_sb = QSpinBox(); self.cap_timeout_sb.setRange(100, 3600000)
        self.cap_timeout_sb.setValue(3000); self.cap_timeout_sb.setSuffix(" ms")

        # ---- 组装（每页一个 QWidget） ----
        self._pages: dict[str, QWidget] = {}

        p_cmd = QWidget(); QFormLayout(p_cmd).addRow("命令", cmd_row)
        self._pages["cmd"] = p_cmd

        p_delay = QWidget(); QFormLayout(p_delay).addRow("延时", self.ms_sb)
        self._pages["delay"] = p_delay

        p_label = QWidget(); QFormLayout(p_label).addRow("标签名", self.label_ed)
        self._pages["label"] = p_label

        p_jump = QWidget()
        jf = QFormLayout(p_jump)
        jf.addRow("条件类型", self.jump_cond_type)
        jf.addRow("条件模式", self.jump_cond_pat)
        cmp_row = QHBoxLayout(); cmp_row.addWidget(self.jump_cond_op); cmp_row.addWidget(self.jump_cond_val, 1)
        jf.addRow("变量比较", cmp_row)
        jf.addRow("跳转到", self.jump_target_cb)
        self._pages["jump"] = p_jump

        p_if = QWidget()
        ff = QFormLayout(p_if)
        ff.addRow("条件列表", self.if_cond)
        ff.addRow("真 → 去向", self.if_then_target)
        then_act = QHBoxLayout(); then_act.addWidget(self.if_then_action_type); then_act.addWidget(self.if_then_action_ed, 1)
        ff.addRow("真 → 动作", then_act)
        ff.addRow("假 → 去向", self.if_else_target)
        else_act = QHBoxLayout(); else_act.addWidget(self.if_else_action_type); else_act.addWidget(self.if_else_action_ed, 1)
        ff.addRow("假 → 动作", else_act)
        self._pages["if"] = p_if

        p_status = QWidget()
        sf = QFormLayout(p_status)
        op_row = QHBoxLayout(); op_row.addWidget(self.status_op); op_row.addWidget(self.status_val, 1)
        sf.addRow("状态属性", self.status_attr)
        sf.addRow("条件", op_row)
        sf.addRow("满足 → 去向", self.status_then_target)
        sthen_act = QHBoxLayout(); sthen_act.addWidget(self.status_then_action_type); sthen_act.addWidget(self.status_then_action_ed, 1)
        sf.addRow("满足 → 动作", sthen_act)
        sf.addRow("不满足 → 去向", self.status_else_target)
        selse_act = QHBoxLayout(); selse_act.addWidget(self.status_else_action_type); selse_act.addWidget(self.status_else_action_ed, 1)
        sf.addRow("不满足 → 动作", selse_act)
        self._pages["status"] = p_status

        p_input = QWidget()
        QFormLayout(p_input).addRow("变量名", self.var_ed)
        QFormLayout(p_input).addRow("提示词", self.prompt_ed)
        QFormLayout(p_input).addRow("超时", self.timeout_sb)
        self._pages["input"] = p_input

        p_trg = QWidget()
        tf = QFormLayout(p_trg)
        tf.addRow("条件列表", self.trg_cond)
        tf.addRow("延时", self.trg_delay_sb)
        tf.addRow("超时", self.trg_timeout_sb)
        self._pages["trigger"] = p_trg

        p_cap = QWidget()
        cf = QFormLayout(p_cap)
        cf.addRow("发送命令", self.cap_cmd_ed)
        cf.addRow("变量名", self.cap_var_ed)
        cf.addRow("检测超时", self.cap_timeout_sb)
        self._pages["captcha"] = p_cap

        self.stack = QStackedWidget()
        for code, _lab in self._STEP_LABELS:
            self.stack.addWidget(self._pages[code])

        form = QFormLayout()
        form.addRow("类型", self.type_cb)
        form.addRow(self.stack)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        box.accepted.connect(self._on_ok)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(box)

        self._populate_targets()
        if default:
            self._load(default)
        self._sync_ui()

    # ---- 目标下拉：已有标签 + 步骤序号 ----
    def _target_options(self) -> list[tuple[str, str]]:
        opts: list[tuple[str, str]] = []
        for i, s in enumerate(self._steps, 1):
            if s.get("type") == "label" and s.get("label"):
                opts.append((s["label"], f"标签 {s['label']}"))
            opts.append((str(i), f"步骤 {i}"))
        return opts

    def _populate_targets(self) -> None:
        for cb in (self.jump_target_cb, self.if_then_target, self.if_else_target,
                   self.status_then_target, self.status_else_target):
            cb.clear()
            for val, lab in self._target_options():
                cb.addItem(lab, val)

    # ---- 类型切换：只显示对应页 ----
    def _sync_ui(self) -> None:
        t = self.type_cb.currentData() or "cmd"
        self.stack.setCurrentIndex(max(0, self.type_cb.findData(t)))

    def _load(self, s: dict) -> None:
        t = s.get("type", "cmd")
        self.type_cb.setCurrentIndex(max(0, self.type_cb.findData(t)))
        self.cmd_ed.setText(s.get("command", ""))
        self.ms_sb.setValue(int(s.get("ms") or s.get("delay_ms") or 500))
        self.label_ed.setText(s.get("label", "") or "")
        self.var_ed.setText(s.get("var", "") or "")
        self.prompt_ed.setText(s.get("prompt", ""))
        self.timeout_sb.setValue(self._timeout_ms(s))

        cond = s.get("condition") or {}
        # 跳转
        jt = cond.get("type")
        if jt in ("contains", "regex", "cmp"):
            self.jump_cond_type.setCurrentIndex(max(0, self.jump_cond_type.findData(jt)))
        self.jump_cond_pat.setText(cond.get("pattern", ""))
        self.jump_cond_op.setCurrentIndex(max(0, self.jump_cond_op.findData(cond.get("op", "="))))
        self.jump_cond_val.setText(cond.get("value", ""))
        self._set_target(self.jump_target_cb, s.get("then"))

        # 判断：conditions + 与/或 + 真/假分支
        if s.get("conditions"):
            self.if_cond.set_conditions(s.get("conditions"))
            self.if_cond.set_relation(s.get("relation", "or"))
        else:
            self.if_cond.set_conditions([cond] if cond else [])
            self.if_cond.set_relation("or")
        then = s.get("then") or {}
        else_ = s.get("else") or {}
        self._set_target(self.if_then_target, then.get("target") if isinstance(then, dict) else then)
        self._set_target(self.if_else_target, else_.get("target") if isinstance(else_, dict) else else_)
        ta = then.get("action") if isinstance(then, dict) else {}
        ea = else_.get("action") if isinstance(else_, dict) else {}
        self._load_action(self.if_then_action_type, self.if_then_action_ed, ta)
        self._load_action(self.if_else_action_type, self.if_else_action_ed, ea)

        # 状态步骤：属性 + 运算符 + 比较值 + 真/假分支
        if t == "status":
            self.status_attr.setCurrentIndex(max(0, self.status_attr.findData(s.get("attr", "qi"))))
            self.status_op.setCurrentIndex(max(0, self.status_op.findData(s.get("op", "="))))
            self.status_val.setText(s.get("value", ""))
            sthen = s.get("then") or {}
            selse = s.get("else") or {}
            self._set_target(self.status_then_target, sthen.get("target") if isinstance(sthen, dict) else sthen)
            self._set_target(self.status_else_target, selse.get("target") if isinstance(selse, dict) else selse)
            self._load_action(self.status_then_action_type, self.status_then_action_ed,
                              sthen.get("action") if isinstance(sthen, dict) else {})
            self._load_action(self.status_else_action_type, self.status_else_action_ed,
                              selse.get("action") if isinstance(selse, dict) else {})

        # 触发器步骤：条件列表（B3 复用）
        if t == "trigger":
            trg_conds = s.get("conditions") or [{"match_type": s.get("match_type", "contains"),
                                                  "pattern": s.get("pattern", "")}]
            self.trg_cond.set_conditions(trg_conds)
            self.trg_cond.set_relation(s.get("relation", "or"))
            self.trg_delay_sb.setValue(int(s.get("delay_ms", 0)))
            self.trg_timeout_sb.setValue(self._timeout_ms(s))

        # 验证码步骤
        if t == "captcha":
            self.cap_cmd_ed.setText(s.get("command", ""))
            self.cap_var_ed.setText(s.get("var", "") or "")
            self.cap_timeout_sb.setValue(self._timeout_ms(s))

    def _set_target(self, cb: QComboBox, val) -> None:
        if val is None:
            return
        idx = cb.findData(str(val))
        cb.setCurrentIndex(max(0, idx))

    @staticmethod
    def _timeout_ms(s: dict) -> int:
        """读取步骤超时（毫秒）：新格式 `timeout_ms` 直接取；旧格式 `timeout`/`timeout_s` 为秒，乘 1000。"""
        if s.get("timeout_ms") not in (None, ""):
            return int(s["timeout_ms"])
        sec = int(s.get("timeout") or s.get("timeout_s") or 0)
        return sec * 1000

    def _load_action(self, type_cb: QComboBox, ed: QLineEdit, act: dict) -> None:
        at = act.get("type") if isinstance(act, dict) else None
        type_cb.setCurrentIndex(max(0, type_cb.findData(at if at in ("cmd", "set") else "none")))
        if at == "cmd":
            ed.setText(act.get("command", ""))
        elif at == "set":
            ed.setText(f"{act.get('var', '')}={act.get('value', '')}")

    def _reverse_cmd(self) -> None:
        self.cmd_ed.setText(reverse_commands(self.cmd_ed.text()))

    # ---- 收集 ----
    def _collect_action(self, type_cb: QComboBox, ed: QLineEdit) -> dict | None:
        at = type_cb.currentData()
        if at == "cmd":
            return {"type": "cmd", "command": ed.text().strip()}
        if at == "set":
            txt = ed.text().strip()
            if "=" in txt:
                var, val = txt.split("=", 1)
                return {"type": "set", "var": var.strip(), "value": val.strip()}
            return {"type": "set", "var": txt.strip(), "value": ""}
        return None

    def _collect_cond(self) -> dict | None:
        jt = self.jump_cond_type.currentData()
        if jt == "none":
            return None
        if jt == "cmp":
            return {"type": "cmp", "var": self.jump_cond_pat.text().strip() or "{v01}",
                    "op": self.jump_cond_op.currentData() or "=", "value": self.jump_cond_val.text().strip()}
        return {"type": jt, "pattern": self.jump_cond_pat.text().strip()}

    def _on_ok(self) -> None:
        t = self.type_cb.currentData()
        s: dict = {"type": t}
        if t == "cmd":
            s["command"] = self.cmd_ed.text().strip()
        elif t == "delay":
            s["ms"] = self.ms_sb.value()
        elif t == "label":
            s["label"] = self.label_ed.text().strip()
        elif t == "jump":
            s["then"] = self.jump_target_cb.currentData() or ""
            cond = self._collect_cond()
            if cond:
                s["condition"] = cond
        elif t == "if":
            s["relation"] = self.if_cond.relation()
            s["conditions"] = list(self.if_cond.conditions)
            then_target = self.if_then_target.currentData() or ""
            else_target = self.if_else_target.currentData() or ""
            then_act = self._collect_action(self.if_then_action_type, self.if_then_action_ed)
            else_act = self._collect_action(self.if_else_action_type, self.if_else_action_ed)
            s["then"] = {"target": then_target}
            if then_act:
                s["then"]["action"] = then_act
            s["else"] = {"target": else_target}
            if else_act:
                s["else"]["action"] = else_act
        elif t == "status":
            s["attr"] = self.status_attr.currentData() or "qi"
            s["op"] = self.status_op.currentData() or "="
            s["value"] = self.status_val.text().strip()
            then_target = self.status_then_target.currentData() or ""
            else_target = self.status_else_target.currentData() or ""
            then_act = self._collect_action(self.status_then_action_type, self.status_then_action_ed)
            else_act = self._collect_action(self.status_else_action_type, self.status_else_action_ed)
            s["then"] = {"target": then_target}
            if then_act:
                s["then"]["action"] = then_act
            s["else"] = {"target": else_target}
            if else_act:
                s["else"]["action"] = else_act
        elif t == "input":
            s["var"] = self.var_ed.text().strip() or "input"
            s["prompt"] = self.prompt_ed.text()
            s["timeout_ms"] = self.timeout_sb.value()
        elif t == "trigger":
            conds = list(self.trg_cond.conditions)
            s["relation"] = self.trg_cond.relation()
            s["conditions"] = conds
            s["match_type"] = conds[0].get("match_type", "contains") if conds else "contains"
            s["pattern"] = conds[0].get("pattern", "") if conds else ""
            s["delay_ms"] = self.trg_delay_sb.value()
            s["timeout_ms"] = self.trg_timeout_sb.value()
        elif t == "captcha":
            s["command"] = self.cap_cmd_ed.text().strip()
            s["var"] = self.cap_var_ed.text().strip() or "captcha"
            s["timeout_ms"] = self.cap_timeout_sb.value()
        self._step = s
        self.accept()

    def result_step(self) -> dict | None:
        return self._step