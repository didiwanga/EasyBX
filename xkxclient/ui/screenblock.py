from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from xkxclient.core.config import ConfigManager

_MATCH_LABELS = [("contains", "包含关键字"), ("regex", "正则匹配")]


class ScreenBlockDialog(QDialog):
    """屏显屏蔽管理：按关键字包含/正则匹配屏蔽主屏输出行。规则存 config.json `screen_block`。"""

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("屏显屏蔽")
        self.setMinimumWidth(420)
        self.config = ConfigManager.instance()
        self.rules: list[dict] = [dict(r) for r in (self.config.get("screen_block") or [])]

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        add_btn = QPushButton("添加")
        edit_btn = QPushButton("编辑")
        del_btn = QPushButton("删除选中")
        add_btn.clicked.connect(self._add)
        edit_btn.clicked.connect(self._edit)
        del_btn.clicked.connect(self._delete)
        self.list.itemDoubleClicked.connect(lambda _i: self._edit())
        btn_row = QHBoxLayout()
        btn_row.addWidget(add_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(del_btn)
        btn_row.addWidget(close_btn := QPushButton("关闭"))
        close_btn.clicked.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(self.list, 1)
        lay.addLayout(btn_row)
        lay.addWidget(close_btn)
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        for r in self.rules:
            self.list.addItem(QListWidgetItem(self._desc(r)))

    def _desc(self, r: dict) -> str:
        mt = r.get("match_type", "contains")
        label = dict(_MATCH_LABELS).get(mt, mt)
        return f"{label}: {r.get('pattern', '')}"

    def _selected(self) -> int:
        return self.list.currentRow()

    def _dlg(self, rule: dict | None = None) -> dict | None:
        dlg = QDialog(self)
        dlg.setWindowTitle("屏蔽规则")
        type_cb = QComboBox()
        for code, lab in _MATCH_LABELS:
            type_cb.addItem(lab, code)
        pat_ed = QLineEdit()
        pat_ed.setPlaceholderText("关键字 或 正则表达式")
        if rule:
            type_cb.setCurrentIndex(max(0, type_cb.findData(rule.get("match_type", "contains"))))
            pat_ed.setText(rule.get("pattern", ""))
        form = QFormLayout()
        form.addRow("类型", type_cb)
        form.addRow("模式", pat_ed)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dlg)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addLayout(form)
        lay.addWidget(box)
        if not dlg.exec():
            return None
        pat = pat_ed.text().strip()
        if not pat:
            return None
        return {"match_type": type_cb.currentData() or "contains", "pattern": pat}

    def _add(self) -> None:
        r = self._dlg()
        if r:
            self.rules.append(r)
            self._refresh()
            self.list.setCurrentRow(len(self.rules) - 1)

    def _edit(self) -> None:
        idx = self._selected()
        if idx < 0 or idx >= len(self.rules):
            return
        r = self._dlg(self.rules[idx])
        if r:
            self.rules[idx] = r
            self._refresh()
            self.list.setCurrentRow(idx)

    def _delete(self) -> None:
        rows = sorted({self.list.row(i) for i in self.list.selectedItems()})
        if not rows:
            idx = self._selected()
            if idx >= 0 and idx < len(self.rules):
                rows = [idx]
        for i in reversed(rows):
            if 0 <= i < len(self.rules):
                self.rules.pop(i)
        self._refresh()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self._delete()
            return
        if event.key() == Qt.Key.Key_A and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.list.selectAll()
            return
        super().keyPressEvent(event)

    def accept(self) -> None:
        self.config.set("screen_block", [dict(r) for r in self.rules])
        if self.session is not None:
            self.session.reload_screen_block()
        super().accept()
