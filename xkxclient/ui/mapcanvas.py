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
        """房间定位：优先使用全局坐标系（实例坐标已按区域/全局对齐）；无坐标房间
        沿已定位邻居方向 BFS 展开；完全孤立房间放到空位（不重叠）。

        探针数据以 [0,0,0] 作为占位锚点（无实际空间意义），room_coords 已过滤；
        网格按 step 对齐，展开时目标格被占则向外螺旋找最近空格，避免重叠。
        """
        step = 90.0
        coord: dict[str, tuple[float, float]] = {}
        grid: set[tuple[float, float]] = set()
        cache = self._cache

        # 统计名字键坐标重复度：同一坐标被多个房间共享说明是占位/合并锚点，不可靠
        nc: dict[tuple[float, float], list[str]] = {}
        for name in cache.rooms:
            c = cache.room_coords(name)
            if c and len(c) >= 2:
                nc.setdefault((c[0] * step, c[1] * step), []).append(name)

        for name in cache.rooms:
            c = cache.room_coords(name)
            if not c or len(c) < 2:
                continue
            pos = (c[0] * step, c[1] * step)
            if len(nc[pos]) > 1:
                continue  # 共享坐标视为锚点，交给 BFS 展开
            coord[name] = pos
            grid.add(pos)

        def free_nearest(gx: float, gy: float) -> tuple[float, float]:
            """从 (gx,gy) 起按半径螺旋找最近未被占用的网格格点。"""
            cands = []
            for r in range(0, 32):
                ring = []
                for k in range(-r, r + 1):
                    ring.append((gx + k * step, gy - r * step))
                    ring.append((gx + k * step, gy + r * step))
                    ring.append((gx - r * step, gy + k * step))
                    ring.append((gx + r * step, gy + k * step))
                for p in ring:
                    if p not in grid:
                        cands.append(p)
                if cands:
                    return cands[0]
            return gx, gy

        while True:
            changed = False
            for name, (px, py) in list(coord.items()):
                for d, nxt in cache.edges.get(name, {}).items():
                    if nxt in coord:
                        continue
                    dx, dy = _DIR_DELTA.get(d, (0, 0))
                    pos = free_nearest(px + dx * step, py + dy * step)
                    coord[nxt] = pos
                    grid.add(pos)
                    changed = True
            if not changed:
                break
        for name in cache.rooms:
            if name in coord:
                continue
            pos = free_nearest(0.0, 0.0)
            coord[name] = pos
            grid.add(pos)
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