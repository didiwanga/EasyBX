from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtWidgets import (
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from xkxclient.core import resources


class MapWindow(QWidget):
    """世界地图窗口（wiki R-资源规范.md）：worldmap.png，滚轮缩放 + 拖拽平移 + 重置。

    滚轮始终缩放（拦截 QScrollArea 视口事件，不滚动内容）；左键拖动平移。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("世界地图")
        self.resize(800, 600)
        self._pixmap = resources.worldmap_pixmap()
        self._scale = 1.0
        self._drag: QPoint | None = None
        self._view_focus: tuple[int, int, float] | None = None

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._refresh()

        self.scroll = QScrollArea(self)
        self.scroll.setWidget(self.label)
        self.scroll.setWidgetResizable(True)
        self.scroll.viewport().installEventFilter(self)

        self.reset_btn = QPushButton("重置")
        self.reset_btn.clicked.connect(self._reset)

        layout = QVBoxLayout(self)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.reset_btn)

    def eventFilter(self, obj, event) -> bool:
        """拦截滚动区滚轮事件：始终缩放，不滚动内容。"""
        if obj is self.scroll.viewport() and event.type() == QEvent.Type.Wheel:
            self.wheelEvent(event)
            return True
        return super().eventFilter(obj, event)

    def _refresh(self) -> None:
        if self._pixmap is None:
            return
        w = max(1, int(self._pixmap.width() * self._scale))
        h = max(1, int(self._pixmap.height() * self._scale))
        self.label.setPixmap(
            self._pixmap.scaled(
                w, h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self._restore_view()

    def _restore_view(self) -> None:
        """缩放后恢复先前视图中心（比例补偿），避免缩放后跳回左上角。"""
        if self._view_focus is None:
            return
        vx, vy, vs = self._view_focus
        if vs <= 0:
            return
        ratio = self._scale / vs
        self.scroll.horizontalScrollBar().setValue(int(vx * ratio))
        self.scroll.verticalScrollBar().setValue(int(vy * ratio))

    def wheelEvent(self, event) -> None:
        factor = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1
        old = self._scale
        self._scale = min(5.0, max(0.1, self._scale * factor))
        # 记录缩放前视图中心（滚动条位置），缩放后按比例恢复，保持鼠标指向的区域
        self._view_focus = (
            self.scroll.horizontalScrollBar().value(),
            self.scroll.verticalScrollBar().value(),
            old,
        )
        self._refresh()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            delta = event.position().toPoint() - self._drag
            self._drag = event.position().toPoint()
            sb = self.scroll.verticalScrollBar().value()
            hb = self.scroll.horizontalScrollBar().value()
            self.scroll.verticalScrollBar().setValue(sb - delta.y())
            self.scroll.horizontalScrollBar().setValue(hb - delta.x())

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        self.unsetCursor()

    def _reset(self) -> None:
        self._scale = 1.0
        self._view_focus = None
        self._refresh()
        self.scroll.verticalScrollBar().setValue(0)
        self.scroll.horizontalScrollBar().setValue(0)