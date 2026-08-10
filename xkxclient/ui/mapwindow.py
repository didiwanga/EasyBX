from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import (
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from xkxclient.core import resources


class MapWindow(QWidget):
    """世界地图窗口（wiki R-资源规范.md）：worldmap.png，滚轮缩放 + 拖拽平移 + 重置。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("世界地图")
        self.resize(800, 600)
        self._pixmap = resources.worldmap_pixmap()
        self._scale = 1.0
        self._drag: QPoint | None = None

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._refresh()

        self.scroll = QScrollArea(self)
        self.scroll.setWidget(self.label)
        self.scroll.setWidgetResizable(True)

        self.reset_btn = QPushButton("重置")
        self.reset_btn.clicked.connect(self._reset)

        layout = QVBoxLayout(self)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.reset_btn)

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

    def wheelEvent(self, event) -> None:
        factor = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1
        self._scale = min(5.0, max(0.1, self._scale * factor))
        self._refresh()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.position().toPoint()

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

    def _reset(self) -> None:
        self._scale = 1.0
        self._refresh()
        self.scroll.verticalScrollBar().setValue(0)
        self.scroll.horizontalScrollBar().setValue(0)