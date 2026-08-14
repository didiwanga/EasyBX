from __future__ import annotations

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from xkxclient.core.config import ConfigManager

_MAX_NOTES = 500


class NotepadDock(QWidget):
    """记事本面板：右键「添加到记事本」的富文本追加到此处，可编辑/删除/清空。

    - 追加内容保留原文本的富文本格式（前景色/背景色/加粗），逐条插入、条目间空行分隔
    - 编辑区可手动增删改；提供「删除选中」「清空」按钮
    - 内容持久化到 config/notepad.txt（HTML），启动自动恢复
    """

    def __init__(self, session=None, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.bus = getattr(session, "app", None).bus if session else None
        self._subscribed = False
        self.setMinimumWidth(220)

        self.editor = QTextEdit()
        self.editor.setPlaceholderText("右键输出区选中文本 → 添加到记事本；此处可编辑/删除。")
        self.del_btn = QPushButton("删除选中")
        self.del_btn.setToolTip("删除编辑区内当前选中的内容")
        self.del_btn.clicked.connect(self._delete_selection)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.setToolTip("清空记事本全部内容")
        self.clear_btn.clicked.connect(self._clear_all)

        btns = QHBoxLayout()
        btns.setSpacing(4)
        btns.addWidget(self.del_btn)
        btns.addWidget(self.clear_btn)
        btns.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(btns)
        lay.addWidget(self.editor, 1)

        if self.bus is not None:
            self._subscribe()

        self._load()

    def _subscribe(self) -> None:
        if self.bus is not None and not self._subscribed:
            self.bus.subscribe("notepad.add", self._on_add)
            self._subscribed = True

    def bind(self, session) -> None:
        self.session = session
        self.bus = getattr(session, "app", None).bus if session else None
        self._subscribe()

    # ---- 追加 ----
    def _on_add(self, payload: dict) -> None:
        frag = payload.get("fragment")
        if frag is None:
            return
        cur = self.editor.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        if not cur.atStart():
            cur.insertBlock()
        cur.insertFragment(frag)
        cur.insertBlock()
        self.editor.setTextCursor(cur)
        self._trim()
        self._save()

    def _delete_selection(self) -> None:
        cur = self.editor.textCursor()
        if cur.hasSelection():
            cur.removeSelectedText()
            self._save()

    def _clear_all(self) -> None:
        self.editor.clear()
        self._save()

    # ---- 上限裁剪 ----
    def _trim(self) -> None:
        doc = self.editor.document()
        if doc.blockCount() <= _MAX_NOTES * 2 + 1:
            return
        # 超出上限：删除最靠前的若干条目（每条目占 2 个块：内容行 + 分隔空行）
        keep_blocks = _MAX_NOTES * 2
        target = doc.findBlockByNumber(doc.blockCount() - keep_blocks).position()
        cur = self.editor.textCursor()
        cur.setPosition(0)
        cur.setPosition(target, cur.MoveMode.KeepAnchor)
        cur.removeSelectedText()

    # ---- 持久化 ----
    def _path(self):
        return ConfigManager.instance().root / "config" / "notepad.html"

    def _load(self) -> None:
        try:
            data = self._path().read_text(encoding="utf-8")
        except OSError:
            return
        if data:
            self.editor.setHtml(data)

    def _save(self) -> None:
        try:
            self._path().parent.mkdir(parents=True, exist_ok=True)
            self._path().write_text(self.editor.toHtml(), encoding="utf-8")
        except OSError:
            pass
