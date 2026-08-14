from __future__ import annotations

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SearchPane(QListWidget):
    """B5 搜索分屏：独立于主屏滚动状态，列出全部命中行，点击定位主输出。"""

    def __init__(self, output, parent=None) -> None:
        super().__init__(parent)
        self._output = output
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.itemClicked.connect(self._on_item)

    def set_hits(self, hits) -> None:
        self.clear()
        for block_no, line in hits:
            item = QListWidgetItem(f"{block_no} | {line[:200]}")
            item.setData(0x0100, block_no)   # Qt.UserRole
            self.addItem(item)

    def _on_item(self, item) -> None:
        block_no = item.data(0x0100)
        if isinstance(block_no, int):
            self._output.go_to_line(block_no)


class FindBar(QWidget):
    """B5 查找栏：Ctrl+F 弹出，自动打开分屏 + 高亮全部命中 + 上/下一个。"""

    def __init__(self, output, parent=None) -> None:
        super().__init__(parent)
        self._output = output
        self.pane = SearchPane(output, self)
        self.pane.hide()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("查找… 回车=下一个, Shift+回车=上一个")
        self.count_label = QLabel("")
        self.prev_btn = QPushButton("↑")
        self.next_btn = QPushButton("↓")
        self.close_btn = QPushButton("✕")
        self.prev_btn.clicked.connect(self._go_prev)
        self.next_btn.clicked.connect(self._go_next)
        self.close_btn.clicked.connect(self.hide)
        self.edit.returnPressed.connect(self._go_next)
        self.edit.textChanged.connect(self._on_text_changed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        bar = QWidget(self)
        blay = QHBoxLayout(bar)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.addWidget(QLabel("查找:"))
        blay.addWidget(self.edit, 1)
        blay.addWidget(self.count_label)
        blay.addWidget(self.prev_btn)
        blay.addWidget(self.next_btn)
        blay.addWidget(self.close_btn)
        lay.addWidget(bar)
        lay.addWidget(self.pane)
        self._output.search_hits_updated.connect(self.pane.set_hits)

    def focus_edit(self) -> None:
        self.edit.selectAll()
        self.edit.setFocus()

    def hide(self) -> None:      # 隐藏时同时收起分屏
        self.pane.hide()
        super().hide()

    def _on_text_changed(self, text: str) -> None:
        if not text:
            self._output.highlight("")   # 仅清高亮与命中分屏，不清主输出
            self.count_label.setText("")
            self.pane.hide()
            return
        self.pane.show()
        n = self._output.highlight(text)
        self.count_label.setText(f"{n}个")

    def _go_next(self) -> None:
        self._move(True)

    def _go_prev(self) -> None:
        self._move(False)

    def _move(self, forward: bool) -> None:
        text = self.edit.text()
        if not text:
            return
        if self._output.match_count() == 0:
            self._output.highlight(text)
        idx = self._output.go_to_match(1 if forward else -1)
        if idx >= 0:
            self.count_label.setText(f"{idx + 1}/{self._output.match_count()}")