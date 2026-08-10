from __future__ import annotations

from PyQt6.QtWidgets import (
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from xkxclient.parse.look import LookResult, RoomStructure


class LookDock(QWidget):
    """E7 房间详情面板：look 解析结果（房间结构/实体/状态）展示 + 手动 look 按钮。"""

    def __init__(self, session=None, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.bus = getattr(session, "app", None).bus if session else None
        self.setMinimumWidth(220)
        self.room_name = QLabel("房间: -")
        self.cat = QLabel("")
        self.exits = QLabel("出口: -")
        self.look_btn = QPushButton("查看当前房间")
        self.look_btn.clicked.connect(self._on_look)
        self.entities = QTextEdit()
        self.entities.setReadOnly(True)
        self.entities.setPlaceholderText("NPC / 物品 / 玩家…")
        self.entities.setMaximumHeight(140)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.look_btn)
        lay.addWidget(self.room_name)
        lay.addWidget(self.cat)
        lay.addWidget(self.exits)
        lay.addWidget(self.entities, 1)

        if self.bus is not None:
            self.bus.subscribe("look.parsed", self._on_parsed)

    def bind(self, session) -> None:
        self.session = session
        self.bus = getattr(session, "app", None).bus if session else None
        self._app = getattr(session, "app", None)
        if self.bus is not None:
            self.bus.subscribe("look.parsed", self._on_parsed)

    def _on_look(self) -> None:
        if self.session is not None and self.session.logged_in:
            self.session._send_look()

    def _on_parsed(self, payload: dict) -> None:
        account = payload.get("account")
        if account is not None and self.session is not None and account != self.session.account_id:
            return
        result = payload.get("result")
        if not isinstance(result, LookResult):
            return
        room = result.room
        if isinstance(room, RoomStructure):
            name = room.name or "-"
            self.room_name.setText(f"房间: {name}")
            self.cat.setText(f"类别: {room.category}" if room.category else "")
            exits = "、".join(room.exits) if room.exits else "-"
            self.exits.setText(f"出口: {exits}")
        lines = []
        for ent in result.entities:
            head = f"[{ent.head}]" if getattr(ent, "head", "") else ""
            lines.append(f"{ent.name} {head}".strip() or "-")
        if lines:
            self.entities.setPlainText("\n".join(lines))
        else:
            self.entities.setPlainText("")
        self.entities.setPlaceholderText("未识别到实体")

    def on_move(self) -> None:
        """GMCP.Move 后：若自动 look 关闭也保持面板名称同步（Room: - 时用短名）。"""
        if self.session is not None and self.session.room_name and "房间: -" in self.room_name.text():
            self.room_name.setText(f"房间: {self.session.room_name}")