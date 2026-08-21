from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from xkxclient.core.config import ConfigManager


class AutoPickupDialog(QDialog):
    """自动拾取设置：开关 + 物品列表（中文名或英文名，如 金条 / Shi tan）。

    规则存 config.json `auto_pickup` = {"enabled": bool, "items": [名字…]}。
    """

    def __init__(self, session=None, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("自动拾取")
        self.setMinimumWidth(380)
        self.config = ConfigManager.instance()
        cfg = self.config.get("auto_pickup") or {}
        self.items: list[str] = [str(x) for x in (cfg.get("items") or [])]

        self.enabled_cb = QCheckBox("启用自动拾取（常驻，监控房间物品）")
        self.enabled_cb.setChecked(bool(cfg.get("enabled", False)))

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        hint = QLabel("物品显示为「中文名(英文名)」如 石炭(Shi tan)。可填中文名或英文名，"
                      "命中后自动发送 get <英文名>。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#808080;")

        self.input = QLineEdit()
        self.input.setPlaceholderText("输入物品名后回车，如 金条 / Shi tan")
        self.input.returnPressed.connect(self._add)

        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self._add)
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self._delete)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.input, 1)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)

        close_btn = QPushButton("保存并关闭")
        close_btn.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(self.enabled_cb)
        lay.addWidget(hint)
        lay.addWidget(self.list, 1)
        lay.addLayout(btn_row)
        lay.addWidget(close_btn)
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        for name in self.items:
            self.list.addItem(QListWidgetItem(name))

    def _add(self) -> None:
        name = self.input.text().strip()
        if not name:
            return
        if name not in self.items:
            self.items.append(name)
            self._refresh()
            self.list.setCurrentRow(len(self.items) - 1)
        self.input.clear()
        self.input.setFocus()

    def _delete(self) -> None:
        rows = sorted({self.list.row(i) for i in self.list.selectedItems()})
        if not rows:
            row = self.list.currentRow()
            if 0 <= row < len(self.items):
                rows = [row]
        for i in reversed(rows):
            if 0 <= i < len(self.items):
                self.items.pop(i)
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
        items = [x for x in self.items if x.strip()]
        self.config.set("auto_pickup", {
            "enabled": self.enabled_cb.isChecked(), "items": items})
        if self.session is not None:
            eng = getattr(self.session, "pickup", None)
            if eng is not None:
                eng.set_config(self.enabled_cb.isChecked(), items)
        super().accept()