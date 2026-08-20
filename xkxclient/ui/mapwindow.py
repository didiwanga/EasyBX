from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from xkxclient.core import resources
from xkxclient.core.config import ConfigManager
from xkxclient.net.ansi import decode_runs
from xkxclient.ui.output import is_mud_wide


def extract_world_map() -> list[list] | None:
    """提取「北侠世界地图」ANSI 块（已固化随客户端分发）。

    优先读取打包资源 materials/worldmap_ansi.bin；无资源时回退
    raw_dump.bin 中的抓包数据（便于用户用最新抓包更新地图）。

    返回 list[list[Span]]（每行一个 span 列表）；找不到返回 None。
    """
    data = resources.worldmap_ansi_bytes()
    if not data:
        try:
            from xkxclient.net.connection import debug_dump_path
            data = debug_dump_path().read_bytes()
        except OSError:
            return None
    if not data:
        return None
    rows: list[list] = []
    started = False
    for raw in data.split(b"\n"):
        spans = decode_runs(raw, "gbk")
        # 剔除行尾 \r（raw_dump 为 \r\n 行尾；\r 会被 QTextDocument 当作段落分隔）
        spans = [s for s in spans if s.text.replace("\r", "")] or []
        if spans:
            spans = [s.__class__(s.text.replace("\r", ""), s.fg, s.bg, s.bold) for s in spans]
        text = "".join(s.text for s in spans)
        if not started:
            if "北侠世界地图" in text:
                started = True
                rows.append(spans)
            continue
        rows.append(spans)
        if text.strip().startswith("└"):
            return rows
        # 分页提示行不作为地图行参与渲染（避免杂行）
        if "未完继续" in text and "%" in text:
            rows.pop()
    return None


class MapWindow(QWidget):
    """世界地图窗口：ANSI 富文本地图（等宽/对齐/颜色，只读）。

    - 地图数据固化在客户端资源 worldmap_ansi.bin，随包分发
    - 滚轮缩放字号、左键拖动平移查看、重置缩放
    - 顶部右侧搜索框：实时搜索地点，命中文本高亮
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("世界地图")
        self.resize(920, 660)
        self._map_lines = extract_world_map()
        self._search_hits: list[QTextCursor] = []
        self._drag: QPoint | None = None
        self._search_term = ""
        self._base_size = 12
        self._build_rich()

    def _build_rich(self) -> None:
        self.search_ed = QLineEdit()
        self.search_ed.setPlaceholderText("搜索地点…")
        self.search_ed.setClearButtonEnabled(True)
        self.search_ed.textChanged.connect(self._search)
        self.search_ed.setMaximumWidth(220)

        top = QHBoxLayout()
        top.addStretch(1)
        top.addWidget(self.search_ed)

        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setUndoRedoEnabled(False)
        self._apply_font()
        if self._map_lines:
            self._populate_map()
        else:
            self.editor.setPlainText("未找到「北侠世界地图」数据")

        self.reset_btn = QPushButton("重置")
        self.reset_btn.setToolTip("重置缩放")
        self.reset_btn.clicked.connect(self._reset_zoom)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.editor, 1)
        lay.addWidget(self.reset_btn)

        self.editor.viewport().installEventFilter(self)

    def _apply_font(self) -> None:
        spec = ConfigManager.instance().get("font", {"family": "SimHei", "size": 12})
        self._base_size = max(6, int(spec.get("size", 12)))
        self._family = spec.get("family", "SimHei")
        self._set_font(self._base_size)

    def _set_font(self, size: int) -> None:
        f = QFont(self._family, size)
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.editor.setFont(f)
        self.editor.setTabStopDistance(
            self.editor.fontMetrics().horizontalAdvance(" ") * 4
        )

    def _populate_map(self) -> None:
        cursor = self.editor.textCursor()
        fm = self.editor.fontMetrics()
        space_adv = fm.horizontalAdvance(" ")
        self._line_orig: list[str] = []
        self._line_pads: list[list[int]] = []
        for idx, spans in enumerate(self._map_lines):
            orig = "".join(s.text for s in spans)
            self._line_orig.append(orig)
            pads = [0]
            for ch in orig:
                add = 1 if is_mud_wide(ch) and fm.horizontalAdvance(ch) < space_adv * 1.75 else 0
                pads.append(pads[-1] + add)
            self._line_pads.append(pads)
            if idx > 0:
                cursor.insertBlock()
            for s in spans:
                fmt = QTextCharFormat()
                if s.fg:
                    fmt.setForeground(QBrush(QColor(f"#{s.fg}")))
                if s.bg:
                    fmt.setBackground(QBrush(QColor(f"#{s.bg}")))
                self._insert_span_text(cursor, s.text, fmt)
        self.editor.setTextCursor(QTextCursor(self.editor.document()))

    def _rebuild(self) -> None:
        """按当前字号重建文档（缩放/重置用）；同时恢复搜索高亮。"""
        self.editor.setExtraSelections([])
        self.editor.clear()
        self._set_font(self._base_size)
        if self._map_lines:
            self._populate_map()
        if self._search_term:
            self._search(self._search_term)

    def _insert_span_text(self, cursor: QTextCursor, text: str, fmt: QTextCharFormat) -> None:
        """插入一个 span 的文本；MUD 全角网格补齐（与主输出一致）。"""
        if not any(is_mud_wide(ch) for ch in text):
            cursor.insertText(text, fmt)
            return
        fm = self.editor.fontMetrics()
        space_adv = fm.horizontalAdvance(" ")
        parts = []
        for ch in text:
            parts.append(ch)
            if is_mud_wide(ch) and fm.horizontalAdvance(ch) < space_adv * 1.75:
                parts.append(" ")
        cursor.insertText("".join(parts), fmt)

    def _search(self, text: str) -> None:
        doc = self.editor.document()
        self._search_hits = []
        self.editor.setExtraSelections([])
        q = text.strip()
        self._search_term = q
        if not q or not self._map_lines:
            return
        fmt = QTextCharFormat()
        fmt.setBackground(QBrush(QColor("#3d5a80")))
        fmt.setForeground(QBrush(QColor("#ffffff")))
        extra = []
        # 在原始文本（未补齐）里搜索，映射回补齐空格后的文档位置
        for li, orig in enumerate(self._line_orig):
            pads = self._line_pads[li]
            block = doc.findBlockByNumber(li)
            bpos = block.position()
            p = 0
            while True:
                idx = orig.find(q, p)
                if idx < 0:
                    break
                start = bpos + idx + pads[idx]
                end = bpos + idx + len(q) + pads[idx + len(q)]
                cur = QTextCursor(doc)
                cur.setPosition(start)
                cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cur
                sel.format = fmt
                extra.append(sel)
                self._search_hits.append(QTextCursor(cur))
                p = idx + 1
        self.editor.setExtraSelections(extra)
        if self._search_hits:
            self.editor.setTextCursor(self._search_hits[0])
            self.editor.centerCursor()

    def _do_zoom(self, delta: int) -> None:
        """滚轮缩放字号：调整基准字号后重建文档。"""
        self._base_size = max(4, min(72, self._base_size + (1 if delta > 0 else -1)))
        self._rebuild()

    def _reset_zoom(self) -> None:
        spec = ConfigManager.instance().get("font", {"family": "SimHei", "size": 12})
        self._base_size = max(6, int(spec.get("size", 12)))
        self._family = spec.get("family", "SimHei")
        self._rebuild()

    # ---------------- 事件：滚轮缩放 + 左键拖动 ----------------
    def eventFilter(self, obj, event) -> bool:
        if getattr(self, "editor", None) is not None and obj is self.editor.viewport():
            t = event.type()
            if t == QEvent.Type.Wheel:
                self._do_zoom(1 if event.angleDelta().y() > 0 else -1)
                return True
            if t == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._drag = event.position().toPoint()
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    return True
            elif t == QEvent.Type.MouseMove:
                if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
                    delta = event.position().toPoint() - self._drag
                    self._drag = event.position().toPoint()
                    v = self.editor.verticalScrollBar()
                    h = self.editor.horizontalScrollBar()
                    v.setValue(v.value() - delta.y())
                    h.setValue(h.value() - delta.x())
                    return True
            elif t == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton and self._drag is not None:
                    self._drag = None
                    self.unsetCursor()
                    return True
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._map_lines:
            self._reset_zoom()
