from __future__ import annotations

from PyQt6.QtWidgets import (
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from xkxclient.core.skills import SkillsParser


class SkillsDock(QWidget):
    """E-skill_panel 技能面板：skills 文本解析 + 分组折叠 + 技能槽。"""

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.parser = SkillsParser()
        self._bound = False
        self.setMinimumWidth(200)
        self.slot_label = QLabel("技能槽 -")
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["技能", "等级", "状态"])
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemClicked.connect(self._on_click)
        self.refresh_btn = QPushButton("刷新技能")
        self.refresh_btn.setToolTip("发送 skills 命令刷新技能面板")
        self.refresh_btn.clicked.connect(self.refresh)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.slot_label)
        lay.addWidget(self.refresh_btn)
        lay.addWidget(self.tree, 1)

    def refresh(self) -> None:
        if self.session is not None and self.session.connected and self.session.logged_in:
            if getattr(self.session, "_debug_skills", False):
                self.session._skills_debug("refresh button clicked")
            self.session.send_skills()

    def bind(self, session) -> None:
        self.session = session
        if self._bound:
            return
        bus = getattr(getattr(session, "app", None), "bus", None)
        if bus is None:
            return
        self._bound = True
        bus.subscribe("GMCP.Skills", self._on_gmcp_event)
        bus.subscribe("GMCP.Combat", self._on_gmcp_event)
        bus.subscribe("GMCP.Buff", self._on_gmcp_event)

    def _on_gmcp_event(self, payload: dict) -> None:
        if self.session is not None and payload.get("account") != self.session.account_id:
            return
        self.on_gmcp(payload.get("module", ""), payload.get("data"))

    def on_skills(self, text: str) -> None:
        self.parser.parse(text)
        self.rebuild()

    def rebuild(self) -> None:
        self.tree.clear()
        if self.parser.slots_total:
            self.slot_label.setText(f"技能槽 {self.parser.slots_used:.1f} / {self.parser.slots_total:.1f}")
        groups: dict[str, QTreeWidgetItem] = {}
        for sk in self.parser.skills:
            if sk.category not in groups:
                grp = QTreeWidgetItem([sk.category, "", ""])
                self.tree.addTopLevelItem(grp)
                groups[sk.category] = grp
            enabled = "启用" if sk.enabled else ""
            lvl = str(sk.level_num) if sk.level_num is not None else sk.level
            groups[sk.category].addChild(QTreeWidgetItem([sk.name, lvl or "", enabled]))
        self.tree.expandAll()

    def _on_click(self, item: QTreeWidgetItem, _col: int) -> None:
        # 行点击 → 发送 `skills <key>`（仅对技能叶子行）
        parent = item.parent()
        if parent is not None:
            name = item.text(0)
            for sk in self.parser.skills:
                if sk.name == name:
                    self.session.connection.send_line(f"skills {sk.key}")
                    break

    def on_gmcp(self, module: str, data) -> None:
        # 预留：GMCP.Skills/Combat/Buff 到达挂点（E-skill_panel 明确以文本解析为主，暂不消费）
        self._last_gmcp = (module, data)