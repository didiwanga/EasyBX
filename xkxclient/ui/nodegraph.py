"""节点图宏编辑器（类 Visio 拖拽连线）。

新宏模式：宏以节点图（nodes + edges）形式存储，保存时写入 automation.json
的 `graph` 字段；运行时由 macro.py 的 `compile_graph` 编译为现有引擎 steps 执行。
本模块提供可视化编辑：节点方块 + 端口拖拽连线，分支节点（if/status）双输出口。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QColor, QPainterPath, QPen, QBrush, QFont, QPainter, QTransform
from PyQt6.QtWidgets import (
    QDialog, QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox,
    QToolBar, QVBoxLayout, QWidget, QGraphicsSceneMouseEvent, QComboBox,
    QFormLayout, QDialogButtonBox,
)

from xkxclient.ui.editors import _STATUS_ATTRS, _STATUS_OPS

# 节点类型元数据：代码 / 中文名 / 颜色 / 输出端口数 / 是否分支
_NODE_TYPES: dict[str, tuple[str, str, int, bool]] = {
    "cmd":          ("命令",          "#4c8bf5", 1, False),
    "delay":        ("延时",          "#7fb74c", 1, False),
    "label":        ("标签",          "#8a8a8a", 1, False),
    "jump":         ("跳转",          "#c792ea", 1, False),
    "if":           ("判断",          "#e06c75", 2, True),
    "status":       ("状态",          "#d19a66", 2, True),
    "hit":          ("等待命中",       "#56b6c2", 1, False),
    "move_trigger": ("移动并触发",     "#f08c3b", 1, False),
    "room":         ("房间",          "#e5c07b", 1, False),
}
_NODE_ORDER = ["cmd", "delay", "label", "jump", "if", "status", "hit", "move_trigger", "room"]

# 端口几何
_INPUT_PORT = "input"    # 左侧入端口
_OUTPUT_PORT = "output"  # 右侧出端口（非分支节点）
_TRUE_PORT = "true"      # 分支真出端口
_FALSE_PORT = "false"    # 分支假出端口

_NODE_W, _NODE_H = 150, 64
_PORT_R = 8
_PORT_HIT = 16           # 端口命中容差（像素）
_PORT_HALF = _PORT_R + 4


def new_node_id(existing: list[str]) -> str:
    """生成不与现有 id 冲突的节点 id（n1, n2, ...）。"""
    used = set(existing)
    i = 1
    while f"n{i}" in used:
        i += 1
    return f"n{i}"


def node_title(t: str) -> str:
    return _NODE_TYPES.get(t, ("?", "", 1, False))[0]


def node_ports(t: str) -> list[str]:
    """返回节点输出端口列表（分支=真/假，其余=output）。"""
    if _NODE_TYPES.get(t, (None, None, 1, False))[3]:
        return [_TRUE_PORT, _FALSE_PORT]
    return [_OUTPUT_PORT]


def node_summary(n: dict) -> str:
    """节点参数摘要（画布上显示）。"""
    t = n.get("type", "cmd")
    if t == "cmd":
        return n.get("command", "")
    if t == "delay":
        return f"{n.get('ms', 0)} ms"
    if t == "label":
        return n.get("label", "")
    if t == "jump":
        return ""
    if t == "if":
        conds = n.get("conditions") or []
        rel = {"and": "且", "or": "或"}.get(n.get("relation", "or"), "或")
        return f"{len(conds)}条件·{rel}" if conds else "无条件"
    if t == "status":
        return f"{n.get('attr', 'qi')} {n.get('op', '=')} {n.get('value', '')}"
    if t == "hit":
        conds = n.get("conditions") or []
        pat = conds[0].get("pattern", "") if conds else n.get("pattern", "")
        return f"{n.get('command', '')} → {pat}"
    if t == "move_trigger":
        conds = n.get("conditions") or []
        pat = conds[0].get("pattern", "") if conds else n.get("pattern", "")
        return f"{n.get('command', '')} → {pat}"
    if t == "room":
        parts = []
        if n.get("exit"):
            parts.append(f"出口:{n['exit']}")
        if n.get("trigger"):
            parts.append(f"等:{n['trigger']}")
        if n.get("command"):
            parts.append(f"令:{n['command']}")
        return " ".join(parts)
    return ""


class EdgeItem(QGraphicsPathItem):
    """连接线：源节点输出端口 → 目标节点输入端口。"""

    def __init__(self, scene: "NodeScene", from_id: str, to_id: str, port: str = "out") -> None:
        super().__init__()
        self.scene_ref = scene
        self.from_id = from_id
        self.to_id = to_id
        self.port = port
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setZValue(-1)
        pen = QPen(QColor("#9aa5b1"))
        pen.setWidthF(1.8)
        self.setPen(pen)
        self.reposition()

    def boundingRect(self) -> QRectF:
        return self.path().boundingRect().adjusted(-6, -6, 6, 6)

    def shape(self) -> QPainterPath:
        return self.path()

    def reposition(self) -> None:
        p1 = self.scene_ref.port_scene_pos(self.from_id, self.port)
        p2 = self.scene_ref.port_scene_pos(self.to_id, _INPUT_PORT)
        if p1 is None or p2 is None:
            return
        dx = max(30.0, (p2.x() - p1.x()) * 0.5)
        path = QPainterPath(p1)
        path.cubicTo(QPointF(p1.x() + dx, p1.y()),
                     QPointF(p2.x() - dx, p2.y()),
                     QPointF(p2.x(), p2.y()))
        self.setPath(path)


class NodeItem(QGraphicsItem):
    """节点方块：标题 + 参数摘要 + 端口。"""

    _NODE_W = _NODE_W
    _NODE_H = _NODE_H

    def __init__(self, scene: "NodeScene", data: dict) -> None:
        super().__init__()
        self.scene_ref = scene
        self.data = data
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(0)
        self._drag_from: str | None = None
        self._build_ports()

    def _build_ports(self) -> None:
        self._ports: dict[str, QPointF] = {}
        t = self.data.get("type", "cmd")
        # 输入端口
        self._ports[_INPUT_PORT] = QPointF(0, _NODE_H / 2)
        # 输出端口
        outs = node_ports(t)
        if len(outs) == 1:
            self._ports[outs[0]] = QPointF(_NODE_W, _NODE_H / 2)
        else:
            self._ports[_TRUE_PORT] = QPointF(_NODE_W, _NODE_H * 0.32)
            self._ports[_FALSE_PORT] = QPointF(_NODE_W, _NODE_H * 0.68)

    def boundingRect(self) -> QRectF:
        return QRectF(-_PORT_HALF, -_PORT_HALF, _NODE_W + _PORT_HALF * 2, _NODE_H + _PORT_HALF * 2)

    def port_scene_pos(self, port: str) -> QPointF:
        if port not in self._ports:
            return QPointF()
        return self.mapToScene(self._ports[port])

    def port_at(self, scene_pos: QPointF) -> str | None:
        """返回场景坐标命中的端口（input 也判）。"""
        for name, local in self._ports.items():
            sp = self.mapToScene(local)
            if (scene_pos - sp).manhattanLength() <= _PORT_HIT:
                return name
        return None

    def paint(self, painter: QPainter, option, widget=None) -> None:
        t = self.data.get("type", "cmd")
        title, color, nout, is_branch = _NODE_TYPES.get(t, ("?", "#888", 1, False))
        selected = self.isSelected()
        # 外框
        rect = QRectF(0, 0, _NODE_W, _NODE_H)
        border = QColor("#ffffff") if selected else QColor("#3a3f4b")
        painter.setPen(QPen(border, 1.8))
        painter.setBrush(QBrush(QColor("#252a34")))
        painter.drawRoundedRect(rect, 6, 6)
        # 标题条
        painter.setBrush(QBrush(QColor(color)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(0, 0, _NODE_W, 22), 6, 6)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.setFont(QFont("Microsoft YaHei", 8, QFont.Weight.Bold))
        painter.drawText(QRectF(6, 0, _NODE_W - 12, 22), Qt.AlignmentFlag.AlignVCenter, title)
        # 摘要
        painter.setFont(QFont("Microsoft YaHei", 8))
        painter.setPen(QPen(QColor("#c8cdd6")))
        text = node_summary(self.data)
        if not text:
            text = node_title(t)
        painter.drawText(QRectF(6, 24, _NODE_W - 12, _NODE_H - 30),
                         Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, text)
        # 端口圆（painter 处于局部坐标，直接用局部坐标画）
        for port, local in self._ports.items():
            if port == _INPUT_PORT:
                pc = QColor("#56b6c2")
            elif port == _TRUE_PORT:
                pc = QColor("#7fb74c")
            elif port == _FALSE_PORT:
                pc = QColor("#e06c75")
            else:
                pc = QColor("#c8cdd6")
            painter.setBrush(QBrush(pc))
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.drawEllipse(local, _PORT_R, _PORT_R)
        # 端口标签
        painter.setFont(QFont("Microsoft YaHei", 7))
        painter.setPen(QPen(QColor("#9aa5b1")))
        if is_branch:
            tp = self._ports[_TRUE_PORT]
            fp = self._ports[_FALSE_PORT]
            painter.drawText(QRectF(tp.x() - 40, tp.y() - 9, 36, 18), Qt.AlignmentFlag.AlignRight, "真")
            painter.drawText(QRectF(fp.x() - 40, fp.y() - 9, 36, 18), Qt.AlignmentFlag.AlignRight, "假")

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.scene_ref.refresh_edges_for(self.data.get("id", ""))
        return super().itemChange(change, value)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        # 交互约定（类 Visio）：
        # - 点中输出端口，或从节点本体任意位置按下 → 从输出端口拖连线
        # - 标题栏（顶部）或 Alt 按下 → 移动节点
        hit = self.port_at(event.scenePos())
        alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        local = self.mapFromScene(event.scenePos())
        on_title = 0 <= local.x() <= _NODE_W and 0 <= local.y() <= 22
        if hit and hit != _INPUT_PORT:
            port = hit
        elif alt or on_title:
            port = None  # 移动节点
        else:
            port = self._nearest_output_port(event.scenePos())
        if port:
            self._drag_from = port
            self.scene_ref.begin_connect(self, port, event.scenePos())
            event.accept()
            return
        self._drag_from = None
        super().mousePressEvent(event)

    def _nearest_output_port(self, scene_pos: QPointF) -> str | None:
        """Alt 拖动时选择距离最近的一个输出端口（分支取最近的真/假）。"""
        best, best_d = None, float("inf")
        for port in node_ports(self.data.get("type", "cmd")):
            sp = self.mapToScene(self._ports[port])
            d = (scene_pos - sp).manhattanLength()
            if d < best_d:
                best, best_d = port, d
        return best

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._drag_from:
            self.scene_ref.update_connect(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._drag_from:
            self.scene_ref.end_connect(event.scenePos())
            self._drag_from = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class NodeScene(QGraphicsScene):
    """画布：管理节点/边，处理拖拽连线交互。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.nodes: dict[str, NodeItem] = {}
        self.edges: list[EdgeItem] = []
        self._connect_from: tuple[NodeItem, str] | None = None
        self._connect_line: QGraphicsPathItem | None = None
        self.setSceneRect(QRectF(-2000, -2000, 6000, 6000))

    # ---- 数据构建 ----
    def load_graph(self, graph: dict) -> None:
        self.clear()
        self.nodes = {}
        self.edges = []
        for n in graph.get("nodes", []):
            self.add_node_item(dict(n))
        for e in graph.get("edges", []):
            self.add_edge(e.get("from"), e.get("to"), e.get("port", "out"))
        self.refresh_edges()

    def graph_data(self) -> dict:
        return {
            "nodes": [it.data for it in self.nodes.values()],
            "edges": [
                {"from": e.from_id, "to": e.to_id, "port": e.port}
                for e in self.edges
            ],
        }

    def add_node_item(self, data: dict) -> NodeItem:
        item = NodeItem(self, data)
        self.addItem(item)
        item.setPos(QPointF(data.get("x", 0), data.get("y", 0)))
        self.nodes[data.get("id", "")] = item
        return item

    def add_node(self, ntype: str, pos: QPointF) -> NodeItem:
        nid = new_node_id(list(self.nodes.keys()))
        data = {"id": nid, "type": ntype, "x": round(pos.x()), "y": round(pos.y())}
        if ntype == "delay":
            data["ms"] = 500
        elif ntype == "label":
            data["label"] = nid
        elif ntype == "hit":
            data["command"] = ""
            data["conditions"] = [{"match_type": "contains", "pattern": ""}]
            data["delay_ms"] = 500
            data["timeout_ms"] = 0
        elif ntype == "move_trigger":
            data["command"] = ""
            data["conditions"] = [{"match_type": "contains", "pattern": ""}]
            data["auto_retry"] = True
            data["delay_ms"] = 500
            data["timeout_ms"] = 5000
        elif ntype == "cmd":
            data["command"] = ""
        elif ntype == "if":
            data["conditions"] = [{"match_type": "contains", "pattern": ""}]
            data["relation"] = "or"
            data["delay_ms"] = 0
            data["timeout_ms"] = 0
        elif ntype == "status":
            data["attr"] = "qi"
            data["op"] = "="
            data["value"] = ""
        return self.add_node_item(data)

    def add_edge(self, from_id: str, to_id: str, port: str = "out") -> bool:
        if from_id not in self.nodes or to_id not in self.nodes:
            return False
        if from_id == to_id:
            return False
        # 同一源端口已有边 → 替换
        self.edges = [e for e in self.edges
                      if not (e.from_id == from_id and e.port == port)]
        e = EdgeItem(self, from_id, to_id, port)
        self.addItem(e)
        e.reposition()  # 创建即计算路径，保证立即可见
        self.edges.append(e)
        return True

    def remove_edge(self, from_id: str, to_id: str, port: str = "out") -> None:
        for e in self.edges:
            if e.from_id == from_id and e.to_id == to_id and e.port == port:
                self.edges.remove(e)
                self.removeItem(e)
                break

    def delete_selected_edges(self) -> None:
        sel = [e for e in self.edges if e.isSelected()]
        for e in sel:
            self.edges.remove(e)
            self.removeItem(e)

    def delete_node(self, nid: str) -> None:
        if nid not in self.nodes:
            return
        item = self.nodes.pop(nid)
        self.removeItem(item)
        for e in list(self.edges):
            if e.from_id == nid or e.to_id == nid:
                self.edges.remove(e)
                self.removeItem(e)

    # ---- 端口坐标 ----
    def port_scene_pos(self, node_id: str, port: str) -> QPointF | None:
        item = self.nodes.get(node_id)
        if item is None:
            return None
        return item.port_scene_pos(port)

    def refresh_edges_for(self, node_id: str) -> None:
        for e in self.edges:
            if e.from_id == node_id or e.to_id == node_id:
                e.reposition()

    def refresh_edges(self) -> None:
        for e in self.edges:
            e.reposition()

    # ---- 拖拽连线 ----
    def begin_connect(self, node: NodeItem, port: str, start_pos: QPointF) -> None:
        self._connect_from = (node, port)
        self._connect_line = QGraphicsPathItem()
        self._connect_line.setZValue(10)
        pen = QPen(QColor("#56b6c2"), 2, Qt.PenStyle.DashLine)
        self._connect_line.setPen(pen)
        self.addItem(self._connect_line)
        self.update_connect(start_pos)

    def update_connect(self, pos: QPointF) -> None:
        if self._connect_line is None or self._connect_from is None:
            return
        start = self._connect_from[0].port_scene_pos(self._connect_from[1])
        path = QPainterPath(start)
        dx = max(20.0, (pos.x() - start.x()) * 0.5)
        path.cubicTo(QPointF(start.x() + dx, start.y()),
                     QPointF(pos.x() - dx, pos.y()),
                     QPointF(pos.x(), pos.y()))
        self._connect_line.setPath(path)

    def end_connect(self, end_pos: QPointF) -> None:
        if self._connect_line is not None:
            self.removeItem(self._connect_line)
            self._connect_line = None
        if self._connect_from is None:
            return
        from_item, from_port = self._connect_from
        self._connect_from = None
        # 命中目标节点的输入端口
        target: NodeItem | None = None
        for item in self.items():
            if isinstance(item, NodeItem) and item is not from_item:
                if item.port_at(end_pos) == _INPUT_PORT:
                    target = item
                    break
        if target is None:
            # 命中节点本体也算（sceneBoundingRect 已含 item 位置）
            for item in self.items():
                if isinstance(item, NodeItem) and item is not from_item:
                    if item.sceneBoundingRect().contains(end_pos):
                        target = item
                        break
        if target is not None:
            self.add_edge(from_item.data.get("id"), target.data.get("id"), from_port)

    def context_menu(self, scene_pos: QPointF) -> QMenu:
        item = self.itemAt(scene_pos, QTransform())
        menu = QMenu()
        if isinstance(item, EdgeItem):
            menu.addAction("删除连线", lambda: self.remove_edge(item.from_id, item.to_id, item.port))
        elif isinstance(item, NodeItem):
            menu.addAction("编辑节点", lambda: None)
            menu.addAction("删除节点", lambda: self.delete_node(item.data.get("id")))
        else:
            for t in _NODE_ORDER:
                menu.addAction(f"添加「{node_title(t)}」", lambda _t=t: self.add_node(_t, scene_pos))
        return menu


class NodeDialog(QDialog):
    """节点参数编辑：按类型显示对应字段。目标（跳转/分支）由连线决定，不在此编辑。"""

    def __init__(self, data: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"编辑节点：{node_title(data.get('type', ''))}")
        self._data = dict(data)
        t = self._data.get("type", "cmd")
        form = QFormLayout()

        if t == "cmd":
            self.command = QLineEdit(self._data.get("command", ""))
            form.addRow("命令", self.command)
        elif t == "delay":
            self.ms = QSpinBox(); self.ms.setRange(0, 86400000)
            self.ms.setValue(int(self._data.get("ms", 500))); self.ms.setSuffix(" ms")
            form.addRow("延时", self.ms)
        elif t == "label":
            self.label = QLineEdit(self._data.get("label", ""))
            form.addRow("标签名", self.label)
        elif t == "jump":
            form.addRow(QLabel("拖拽右侧端口连线到目标节点"))
        elif t == "if":
            form.addRow(QLabel("条件（真→绿色端口，假→红色端口，拖线连接）"))
            self.if_cond = _CondListWidget(self._data.get("conditions") or [])
            self.if_rel = QComboBox()
            self.if_rel.addItem("任一满足(或)", "or")
            self.if_rel.addItem("全部满足(且)", "and")
            self.if_rel.setCurrentIndex(1 if self._data.get("relation") == "and" else 0)
            self.if_delay = QSpinBox(); self.if_delay.setRange(0, 3600000)
            self.if_delay.setValue(int(self._data.get("delay_ms", 0))); self.if_delay.setSuffix(" ms")
            self.if_timeout = QSpinBox(); self.if_timeout.setRange(0, 3600000)
            self.if_timeout.setValue(int(self._data.get("timeout_ms", 0))); self.if_timeout.setSuffix(" ms")
            form.addRow(self.if_cond)
            form.addRow("关系", self.if_rel)
            form.addRow("命中后延时", self.if_delay)
            form.addRow("等待超时", self.if_timeout)
        elif t == "status":
            form.addRow(QLabel("状态比较（真→绿色端口，假→红色端口，拖线连接）"))
            self.status_attr = QComboBox()
            for key, lab in _STATUS_ATTRS:
                self.status_attr.addItem(lab, key)
            idx = self.status_attr.findData(self._data.get("attr", "qi"))
            self.status_attr.setCurrentIndex(max(0, idx))
            self.status_op = QComboBox()
            for op, lab in _STATUS_OPS:
                self.status_op.addItem(lab, op)
            oidx = self.status_op.findData(self._data.get("op", "="))
            self.status_op.setCurrentIndex(max(0, oidx))
            self.status_val = QLineEdit(self._data.get("value", ""))
            form.addRow("属性", self.status_attr)
            form.addRow("比较", self.status_op)
            form.addRow("数值", self.status_val)
        elif t == "hit":
            self.hit_cmd = QLineEdit(self._data.get("command", ""))
            conds = self._data.get("conditions") or [{"match_type": "contains", "pattern": ""}]
            self.hit_pat = QLineEdit(conds[0].get("pattern", "") if conds else "")
            self.hit_delay = QSpinBox(); self.hit_delay.setRange(0, 3600000)
            self.hit_delay.setValue(int(self._data.get("delay_ms", 500))); self.hit_delay.setSuffix(" ms")
            self.hit_timeout = QSpinBox(); self.hit_timeout.setRange(0, 3600000)
            self.hit_timeout.setValue(int(self._data.get("timeout_ms", 0))); self.hit_timeout.setSuffix(" ms")
            form.addRow("命令", self.hit_cmd)
            form.addRow("命中文本", self.hit_pat)
            form.addRow("重发间隔", self.hit_delay)
            form.addRow("超时终止", self.hit_timeout)
        elif t == "move_trigger":
            self.mt_cmd = QLineEdit(self._data.get("command", ""))
            self.mt_cmd.setPlaceholderText("如 north;(south;west)")
            conds = self._data.get("conditions") or [{"match_type": "contains", "pattern": ""}]
            self.mt_pat = QLineEdit(conds[0].get("pattern", "") if conds else "")
            self.mt_delay = QSpinBox(); self.mt_delay.setRange(0, 3600000)
            self.mt_delay.setValue(int(self._data.get("delay_ms", 500))); self.mt_delay.setSuffix(" ms")
            self.mt_timeout = QSpinBox(); self.mt_timeout.setRange(0, 3600000)
            self.mt_timeout.setValue(int(self._data.get("timeout_ms", 5000))); self.mt_timeout.setSuffix(" ms")
            form.addRow("移动命令", self.mt_cmd)
            form.addRow("目标文本", self.mt_pat)
            form.addRow("步间延时", self.mt_delay)
            form.addRow("超时", self.mt_timeout)
        elif t == "room":
            form.addRow(QLabel("房间：从当前房间经指定出口移动，到达后可等待触发或执行命令"))
            self.rm_exit = QLineEdit(self._data.get("exit", ""))
            self.rm_exit.setPlaceholderText("如 east")
            self.rm_trigger = QLineEdit(self._data.get("trigger", ""))
            self.rm_trigger.setPlaceholderText("到达后等待的文本，留空则移动后直接继续")
            self.rm_command = QLineEdit(self._data.get("command", ""))
            self.rm_command.setPlaceholderText("到达后执行的命令（可选）")
            self.rm_delay = QSpinBox(); self.rm_delay.setRange(0, 3600000)
            self.rm_delay.setValue(int(self._data.get("delay_ms", 500))); self.rm_delay.setSuffix(" ms")
            self.rm_timeout = QSpinBox(); self.rm_timeout.setRange(0, 3600000)
            self.rm_timeout.setValue(int(self._data.get("timeout_ms", 5000))); self.rm_timeout.setSuffix(" ms")
            form.addRow("实际出口", self.rm_exit)
            form.addRow("到达触发", self.rm_trigger)
            form.addRow("到达命令", self.rm_command)
            form.addRow("移动延时", self.rm_delay)
            form.addRow("超时", self.rm_timeout)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(btns)

    def result_data(self) -> dict:
        t = self._data.get("type", "cmd")
        d = dict(self._data)
        if t == "cmd":
            d["command"] = self.command.text()
        elif t == "delay":
            d["ms"] = self.ms.value()
        elif t == "label":
            d["label"] = self.label.text()
        elif t == "if":
            d["conditions"] = self.if_cond.conditions()
            d["relation"] = self.if_rel.currentData()
            d["delay_ms"] = self.if_delay.value()
            d["timeout_ms"] = self.if_timeout.value()
        elif t == "status":
            d["attr"] = self.status_attr.currentData()
            d["op"] = self.status_op.currentData()
            d["value"] = self.status_val.text()
        elif t == "hit":
            d["command"] = self.hit_cmd.text()
            d["conditions"] = [{"match_type": "contains", "pattern": self.hit_pat.text()}]
            d["delay_ms"] = self.hit_delay.value()
            d["timeout_ms"] = self.hit_timeout.value()
        elif t == "move_trigger":
            d["command"] = self.mt_cmd.text()
            d["conditions"] = [{"match_type": "contains", "pattern": self.mt_pat.text()}]
            d["delay_ms"] = self.mt_delay.value()
            d["timeout_ms"] = self.mt_timeout.value()
        elif t == "room":
            d["exit"] = self.rm_exit.text()
            d["trigger"] = self.rm_trigger.text()
            d["command"] = self.rm_command.text()
            d["delay_ms"] = self.rm_delay.value()
            d["timeout_ms"] = self.rm_timeout.value()
        return d


class _CondListWidget(QWidget):
    """简化条件列表：contains 文本一行一个。"""

    def __init__(self, conds: list[dict]) -> None:
        super().__init__()
        self._conds = list(conds)
        self._build()

    def _build(self) -> None:
        from PyQt6.QtWidgets import QListWidget
        self.list = QListWidget()
        for c in self._conds:
            self.list.addItem(c.get("pattern", ""))
        self.list.setMinimumHeight(60)
        add = QPushButton("＋条件")
        add.clicked.connect(lambda: (self.list.addItem(""), ))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.list, 1)
        lay.addWidget(add)

    def conditions(self) -> list[dict]:
        out = []
        for i in range(self.list.count()):
            pat = self.list.item(i).text()
            out.append({"match_type": "contains", "pattern": pat})
        return out


class NodeGraphEditor(QDialog):
    """节点图宏编辑器：画布 + 工具条 + 保存到 automation.json。"""

    def __init__(self, session, name: str = "", graph: dict | None = None,
                 parent=None, on_saved=None) -> None:
        super().__init__(parent)
        self.session = session
        self._name = name
        self._on_saved_cb = on_saved   # 保存成功后回调(new_name, graph)，供宿主同步缓存
        self.setWindowTitle(f"节点图宏：{name or '新建'}")
        self.resize(1100, 700)

        # 工具条
        tb = QToolBar("节点")
        tb.setMovable(False)
        for t in _NODE_ORDER:
            act = tb.addAction(f"＋{node_title(t)}")
            act.triggered.connect(lambda _c=False, _t=t: self._add_node_center(_t))
        tb.addSeparator()
        del_act = tb.addAction("删除选中")
        del_act.triggered.connect(self._delete_selected)

        # 画布
        self.scene = NodeScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._on_context_menu)

        # 名称行
        self.name_ed = QLineEdit(name)
        self.name_ed.setPlaceholderText("宏名称")

        # 按钮
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._on_save)
        close_btn = QPushButton("取消")
        close_btn.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addWidget(QLabel("名称:"))
        btn_row.addWidget(self.name_ed, 1)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        btn_row.addWidget(close_btn)

        lay = QVBoxLayout(self)
        lay.addWidget(tb)
        lay.addLayout(btn_row)
        lay.addWidget(self.view, 1)
        lay.addWidget(QLabel("连线：从节点任意位置按住拖到目标节点松开；拖动节点顶部标题条或按 Alt 移动；右键节点编辑/删除"))

        if graph:
            self.scene.load_graph(graph)

    # ---- 交互 ----
    def _add_node_center(self, ntype: str) -> None:
        # 新节点错开摆放（避免全部重叠在画布中心，难以区分/连线）
        base = self.view.mapToScene(self.view.viewport().rect().center())
        n = len(self.scene.nodes)
        offset = QPointF((n % 4) * 180, (n // 4) * 110)
        self.scene.add_node(ntype, base + offset)

    def _delete_selected(self) -> None:
        self.scene.delete_selected_edges()
        for item in list(self.scene.selectedItems()):
            if isinstance(item, NodeItem):
                self.scene.delete_node(item.data.get("id"))

    def _on_context_menu(self, pos) -> None:
        scene_pos = self.view.mapToScene(pos)
        item = self.scene.itemAt(scene_pos, QTransform())
        menu = self.scene.context_menu(scene_pos)
        if isinstance(item, NodeItem):
            # 节点：在「编辑节点」动作上追加打开参数编辑
            for act in menu.actions():
                if act.text() == "编辑节点":
                    act.triggered.connect(lambda _c=False: self._edit_node(item))
        menu.exec(self.view.mapToGlobal(pos))

    def _edit_node(self, item: NodeItem) -> None:
        dlg = NodeDialog(item.data, self)
        if dlg.exec() and dlg.result_data():
            item.data = dlg.result_data()
            item._build_ports()
            self.scene.refresh_edges_for(item.data.get("id"))
            item.update()

    def _on_save(self) -> None:
        name = self.name_ed.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入宏名称")
            return
        graph = self.scene.graph_data()
        if not graph.get("nodes"):
            QMessageBox.warning(self, "提示", "画布为空，请先添加节点")
            return
        self._save_macro(name, graph)
        self.accept()

    def _save_macro(self, name: str, graph: dict) -> None:
        """把节点图宏写入账号 automation.json 的 macros 列表（整体替换同名项）。"""
        from xkxclient.automation.macro import compile_graph
        try:
            steps = compile_graph(graph)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "校验失败", f"节点图无法编译：{e}")
            return
        if not steps:
            QMessageBox.warning(self, "校验失败", "节点图没有可执行内容")
            return

        cfg = self.session.app.config
        acc = self.session.account_id
        data = {d["name"]: dict(d) for d in cfg.automation(acc)["macros"]
                if not d.get("shared")}
        data[name] = {"name": name, "enabled": True, "shared": False,
                      "group": "", "graph": graph}
        cfg.save_automation(acc, "macros", list(data.values()))
        self.session.reload_automation()
        if self._on_saved_cb is not None:
            try:
                self._on_saved_cb(name, graph)
            except Exception:  # noqa: BLE001
                pass   # 宿主同步失败不影响主流程
        bus = getattr(self.session, "app", None)
        bus = getattr(bus, "bus", None) if bus is not None else None
        if bus is not None:
            bus.publish("automation.saved", account=acc, kind="macros")
            bus.publish("ui.message", account=acc, message=f"节点图宏「{name}」已保存")
