from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QMouseEvent, QTextBlockFormat, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from xkxclient.core import config as cfg


class ChannelBar(QWidget):
    """B5e 聊天栏：底部频道实时输出（富文本，同主输出字体/着色）。

    恒开（无总开关）；默认高度 2 行，可拖至最小 1 行，最高不超过主输出区 50%。
    拖动顶部的细手柄上下即可改高度；双击手柄复位默认 2 行。
    频道行只进聊天栏，不进主输出（B5e 恒开路由）。
    """

    _DEFAULT_ROWS = 2
    _MIN_ROWS = 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._channels: dict[str, bool] = {}
        self.output = QPlainTextEdit(self)
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(0)
        self.output.setPlaceholderText("聊天栏：实时频道消息")
        self._apply_font(cfg.ConfigManager.instance().get("font", {"family": "SimHei", "size": 12}))

        # 细拖拽手柄：调整聊天栏高度，双击复位
        self.handle = QWidget(self)
        self.handle.setFixedHeight(6)
        self.handle.setCursor(Qt.CursorShape.SizeVerCursor)
        self.handle.mousePressEvent = self._press
        self.handle.mouseMoveEvent = self._move
        self.handle.mouseReleaseEvent = self._release
        self.handle.mouseDoubleClickEvent = lambda _e: self._reset_height()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.handle)
        lay.addWidget(self.output, 1)

        self._drag_y: int | None = None
        self._drag_h: int = 0
        self._reset_height()

    def _apply_font(self, spec: dict) -> None:
        f = QFont(spec.get("family", "Consolas"), int(spec.get("size", 12)))
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.output.setFont(f)

    def rows_height(self) -> int:
        fm = self.output.fontMetrics()
        return fm.height() + self.output.frameWidth() * 2 + 2

    def _min_h(self) -> int:
        return self.handle.height() + self._MIN_ROWS * self.rows_height()

    def _max_h(self) -> int:
        """最高不超过主输出区（父容器）50%。"""
        base = self.handle.height() + 50 * self.rows_height()
        if self.parent() is not None:
            base = min(base, int(self.parent().height() * 0.5))
        return base

    def _reset_height(self) -> None:
        self.setFixedHeight(self._min_h() + self._DEFAULT_ROWS * self.rows_height())

    def _press(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_y = e.globalPosition().y()
            self._drag_h = self.height()
            e.accept()

    def _move(self, e: QMouseEvent) -> None:
        if self._drag_y is None:
            return
        d = int(e.globalPosition().y() - self._drag_y)
        # 向上拖（d<0）→ 高度增加；向下拖（d>0）→ 高度减少（顶部边界在上方）
        self.setFixedHeight(min(self._max_h(), max(self._min_h(), self._drag_h - d)))
        e.accept()

    def _release(self, e: QMouseEvent) -> None:
        self._drag_y = None
        e.accept()

    def set_channels(self, channels: dict[str, bool]) -> None:
        self._channels.update(channels or {})

    def append(self, name: str, spans: list, highlight: bool = False) -> None:
        """富文本追加：按 spans 逐段着色，字体同主输出（B5e）。"""
        if not self._channels.get(name, True):
            return
        cursor = QTextCursor(self.output.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if highlight:
            bg = QTextBlockFormat()
            bg.setBackground(QColor("#3d3410"))
            cursor.setBlockFormat(bg)
        # 频道前缀淡色标识
        prefix = QTextCharFormat()
        prefix.setForeground(QColor("#808080"))
        cursor.insertText(f"【{name}】", prefix)
        for s in spans or []:
            fmt = QTextCharFormat()
            if s.fg:
                fmt.setForeground(QColor("#" + s.fg))
            if s.bg:
                fmt.setBackground(QColor("#" + s.bg))
            cursor.insertText(getattr(s, "text", ""), fmt)
        cursor.insertText("\n")
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()
        sb = self.output.verticalScrollBar()
        sb.setValue(sb.maximum())