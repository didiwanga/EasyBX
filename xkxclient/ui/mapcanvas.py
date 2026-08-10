from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

_NODE_R = 22

# 方向感知 BFS 布局方位向量（地图暖航用，坐标单位=步距）
_DIR_DELTA = {
    "north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0),
    "northeast": (1, -1), "northwest": (-1, -1),
    "southeast": (1, 1), "southwest": (-1, 1),
    "up": (0, -1.6), "down": (0, 1.6),
}
_VERTICAL_DIRS = {"up", "down", "northup", "southup", "eastup", "westup",
                  "northdown", "southdown", "eastdown", "westdown"}


class RoomNode(QGraphicsEllipseItem):
    """房间节点：名称标签 + 当前位置金环高亮。"""

    def __init__(self, name: str, x: float, y: float, current: bool = False,
                 category: str = "") -> None:
        r = _NODE_R
        super().__init__(-r / 2, -r / 2, r, r)
        self.room_name = name
        if current:
            self.setBrush(QBrush(QColor("#c89622")))   # 当前位置金环
            self.setPen(QPen(QColor("#ffe08a"), 3))
        else:
            self.setBrush(QBrush(QColor("#2b3a4b")))
            self.setPen(QPen(QColor("#7fa9c8") if not category else QColor("#8fc07a"), 2))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(1)
        self.setToolTip(f"房间: {name}" + (f" [{category}]" if category else ""))

        txt = QGraphicsSimpleTextItem(name, self)
        txt.setFont(QFont("SimHei", 9))
        txt.setBrush(QBrush(QColor("#e0e0e0")))
        txt.setPos(-r / 2 + 2, -r / 2 - 16)

    def pos_center(self) -> QPointF:
        return QPointF(self.x(), self.y())


class EdgeItem(QGraphicsPathItem):
    """方向连线：单向/垂直方向用虚线，双向实线。"""

    def __init__(self, a: QPointF, b: QPointF, one_way: bool, vertical: bool) -> None:
        super().__init__()
        from PyQt6.QtGui import QPainterPath
        path = QPainterPath(a)
        path.lineTo(b)
        self.setPath(path)
        if one_way or vertical:
            from PyQt6.QtCore import Qt as _Qt
            self.setPen(QPen(QColor("#777777"), 1, _Qt.PenStyle.DashLine))
        else:
            self.setPen(QPen(QColor("#4a5a6a"), 1))
        self.setZValue(0)


class LocalMapWidget(QWidget):
    """本地房间拓扑矢量画布（地图系统.md「呈现」）。

    方向感知 BFS 布局；当前位置金环高亮；滚轮缩放 / 拖拽平移 / 点击高亮 / 搜索定位。
    """

    def __init__(self, cache, parent=None) -> None:
        super().__init__(parent)
        self._cache = cache
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索房间…")
        self.search.textChanged.connect(self._on_search)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.search)
        lay.addWidget(self.view, 1)
        self._last_hit: QGraphicsItem | None = None

    # ---- 布局 ----
    def _layout_coords(self) -> dict[str, tuple[float, float]]:
        """方向感知 BFS 布局：从当前房间向外扩展，同方向并列避让。"""
        step = 90.0
        coord: dict[str, tuple[float, float]] = {}
        cur = self._cache.current
        if cur:
            coord[cur] = (0.0, 0.0)
        while True:
            changed = False
            for name, (px, py) in list(coord.items()):
                for d, nxt in self._cache.edges.get(name, {}).items():
                    if nxt in coord:
                        continue
                    dx, dy = _DIR_DELTA.get(d, (0, 0))
                    x, y = px + dx * step, py + dy * step
                    # 冲突避让：已占用则斜向错一位
                    while True:
                        clash = any(abs(ex - x) < step * 0.4 and abs(ey - y) < step * 0.4
                                    for (ex, ey) in coord.values())
                        if not clash:
                            break
                        x += step * 0.5
                        y += step * 0.5
                    coord[nxt] = (x, y)
                    changed = True
            if not changed:
                break
        return coord

    def reload(self) -> None:
        self.scene.clear()
        coord = self._layout_coords()
        if not coord:
            self.scene.setSceneRect(QRectF(0, 0, 1, 1))
            return
        cur = self._cache.current or ""
        nodes: dict[str, QGraphicsEllipseItem] = {}
        for name, (x, y) in coord.items():
            cat = self._cache.rooms.get(name, {}).get("category", "")
            node = RoomNode(name, x, y, current=(name == cur), category=cat)
            self.scene.addItem(node)
            nodes[name] = node
        for frm, edges in self._cache.edges.items():
            if frm not in nodes:
                continue
            for d, nxt in edges.items():
                if nxt not in nodes or d not in _DIR_DELTA:
                    continue
                a = QPointF(nodes[frm].x(), nodes[frm].y())
                b = QPointF(nodes[nxt].x(), nodes[nxt].y())
                reverse = self._cache.edges.get(nxt, {}).get({
                    "north": "south", "south": "north", "east": "west", "west": "east",
                    "up": "down", "down": "up",
                }.get(d, ""), "") == frm
                self.scene.addItem(EdgeItem(a, b, one_way=not reverse, vertical=d in _VERTICAL_DIRS))
        items = list(self.scene.items())
        if items:
            self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-60, -60, 60, 60))
            self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # ---- 交互 ----
    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.view.scale(factor, factor)

    def _on_search(self, text: str) -> None:
        if self._last_hit is not None:
            self._last_hit.setBrush(QBrush(QColor("#e0e0e0")))
            self._last_hit = None
        if not text:
            return
        for item in self.scene.items():
            if isinstance(item, QGraphicsSimpleTextItem):
                if text.lower() in item.text().lower():
                    self.view.centerOn(item)
                    item.setBrush(QBrush(QColor("#ffd700")))
                    self._last_hit = item
                    return