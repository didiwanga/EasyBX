from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from xkxclient.core.config import json_read
from xkxclient.core.resources import PROJECT_ROOT


class DslManualPanel(QWidget):
    """B3c DSL 手册面板：API 手册从数据文件 resources/dsl_api.json 加载，UI 零硬编码。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜 API 名/描述…")
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.search.textChanged.connect(self._on_search)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(self.search)
        lay.addWidget(self.tree, 1)

        self._load()
        self.tree.itemClicked.connect(self._on_click)

    def _data_path(self):
        return PROJECT_ROOT / "resources" / "dsl_api.json"

    def _load(self) -> None:
        data = json_read(self._data_path())
        self.tree.clear()
        namespaces = data.get("namespaces", []) if isinstance(data, dict) else []
        for ns in namespaces:
            ns_node = QTreeWidgetItem([ns.get("name", ns.get("ns", "?"))])
            ns_node.setData(0, Qt.ItemDataRole.UserRole, ns)
            self.tree.addTopLevelItem(ns_node)
            for fn in ns.get("functions", []):
                label = fn.get("signature", fn.get("name", ""))
                item = QTreeWidgetItem([label])
                item.setToolTip(0, fn.get("desc", ""))
                item.setData(0, Qt.ItemDataRole.UserRole, fn)
                ns_node.addChild(item)
        self.tree.expandAll()

    def _on_search(self, text: str) -> None:
        kw = text.strip().lower()
        matched = None
        if kw:
            matched = set()
            data = json_read(self._data_path())
            for ns in data.get("namespaces", []) if isinstance(data, dict) else []:
                for fn in ns.get("functions", []):
                    hay = " ".join(str(fn.get(k, "")) for k in ("name", "signature", "desc")).lower()
                    if kw in hay:
                        matched.add(fn.get("name", ""))
        for i in range(self.tree.topLevelItemCount()):
            ns_node = self.tree.topLevelItem(i)
            visible_children = 0
            for j in range(ns_node.childCount()):
                item = ns_node.child(j)
                fn = item.data(0, Qt.ItemDataRole.UserRole) or {}
                show = matched is None or fn.get("name") in matched
                item.setHidden(not show)
                if show:
                    visible_children += 1
            ns_node.setHidden(matched is not None and visible_children == 0)
        self.tree.expandAll()

    def _on_click(self, item: QTreeWidgetItem, _col: int) -> None:
        fn = item.data(0, Qt.ItemDataRole.UserRole)
        if fn and isinstance(fn, dict) and "signature" in fn:
            desc = fn.get("desc", "")
            if desc:
                self.setToolTip(desc)
