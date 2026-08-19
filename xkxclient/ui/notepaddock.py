from __future__ import annotations

from PyQt6.QtGui import QFont
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

        self._font_zoom = 0
        self.editor = QTextEdit()
        self.editor.setPlaceholderText("右键输出区选中文本 → 添加到记事本；此处可编辑/删除。")
        self._apply_font()
        self.del_btn = QPushButton("删除选中")
        self.del_btn.setToolTip("删除编辑区内当前选中的内容")
        self.del_btn.clicked.connect(self._delete_selection)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.setToolTip("清空记事本全部内容")
        self.clear_btn.clicked.connect(self._clear_all)
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setToolTip("放大记事本字体")
        self.zoom_in_btn.clicked.connect(self._zoom_in)
        self.zoom_out_btn = QPushButton("-")
        self.zoom_out_btn.setToolTip("缩小记事本字体")
        self.zoom_out_btn.clicked.connect(self._zoom_out)

        btns = QHBoxLayout()
        btns.setSpacing(4)
        btns.addWidget(self.del_btn)
        btns.addWidget(self.clear_btn)
        btns.addStretch(1)
        btns.addWidget(self.zoom_out_btn)
        btns.addWidget(self.zoom_in_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(btns)
        lay.addWidget(self.editor, 1)

        if self.bus is not None:
            self._subscribe()

        self._load()

    def _zoom_in(self) -> None:
        self._font_zoom = min(self._font_zoom + 1, 20)
        # QTextEdit.zoomIn 对整个文档生效（含 setHtml 加载的旧信息内联字号），
        # 避免仅 setFont 改默认字体时旧内容无法缩放
        self.editor.zoomIn(1)

    def _zoom_out(self) -> None:
        self._font_zoom = max(self._font_zoom - 1, -10)
        self.editor.zoomOut(1)

    def _apply_font(self) -> None:
        """与主输出同款渲染：等宽字体 + 制表位=空格宽×4 + 不换行，保证 MUD 表格对齐。"""
        spec = ConfigManager.instance().get("font", {"family": "SimHei", "size": 12})
        size = max(6, int(spec.get("size", 12)) + self._font_zoom)
        f = QFont(spec.get("family", "SimHei"), size)
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.editor.setFont(f)
        self.editor.setTabStopDistance(self.editor.fontMetrics().horizontalAdvance(" ") * 4)
        self.editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

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
