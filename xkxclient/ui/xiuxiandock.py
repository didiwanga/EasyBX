from __future__ import annotations

import re

from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# 每组: (组名, 组说明tooltip, [(按钮名, 命令模板), ...])
# 命令模板中 {xxx} 为占位符，点击时弹窗收集后拼装发送。
_GROUPS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "修炼提升",
        "练功总览：修炼特殊内功 / 打坐吐纳 / 练习 / 冥想。修炼特内功须 100 级后，十倍消耗潜能；"
        "读书=练习=领悟(免费) > 学习 > 切磋 > 修炼。技能上限=(exp*10)^(1/3)+1（30悟性）。",
        [
            ("修炼内功", "xiulian {内功名}"),
            ("打坐 (dazuo)", "dazuo {次数}"),
            ("吐纳 (tuna)", "tuna {次数}"),
            ("练习 (lian)", "lian {武功} {次数}"),
            ("学习 (xue)", "xue {师父} {技能} {次数}"),
            ("冥想 (mingxiang)", "mingxiang"),
        ],
    ),
    (
        "天赋属性",
        "先天影响战斗：命中=50%身法+25%根骨+25%悟性；伤害100%膂力；躲闪100%身法；防御50%根骨+25%身法+25%膂力。"
        "悟性±10%技能上限。后天=基本功夫/10。可用 changegift 调天赋（前4项合计80，10-30）。",
        [
            ("先天属性 (sc -xiantian)", "sc -xiantian"),
            ("后天属性 (sc -houtian)", "sc -houtian"),
            ("战斗相关 (sc)", "sc"),
            ("调天赋 (changegift)", "changegift {数值xx xx xx xx xx xx xx}"),
            ("占卜天赋 (zhanbu -gift)", "zhanbu -gift"),
            ("冥想提灵性 (mingxiang)", "mingxiang"),
        ],
    ),
    (
        "技能提高",
        "读书=练习=领悟不耗潜能；学习正常耗潜能；切磋五倍、修炼十倍。练习条件：基本武功>特殊武功且已激发，"
        "空手功夫需空手、兵器功夫需持兵刃；读书: du/read 书名 1；领悟需先过罗汉大阵且特殊>=基本。",
        [
            ("技能列表 (skills)", "skills"),
            ("读书 (du)", "du {书名} 1"),
            ("练习 (lian)", "lian {武功} 1"),
            ("学习 (xue)", "xue {师父} {技能} 1"),
            ("领悟 (lingwu)", "lingwu {武功} 1"),
            ("研修查看 (biguan -qb)", "biguan -qb {技能}"),
        ],
    ),
    (
        "经脉系统",
        "Lv30+ 可通脉。vein through <穴道名> 每穴每日1000次，须内力>100%。正逆通效果不同；逆通2/3概率约1/3收益。"
        "走火后：1-2层可 ask 北丑 经脉受损 修复，3层起需走火药。",
        [
            ("经脉总览 (vein)", "vein"),
            ("通脉大局 (vein overall)", "vein overall"),
            ("经脉列表 (vein list)", "vein list"),
            ("通脉详情 (vein detail)", "vein detail"),
            ("贯通 (vein through)", "vein through {穴道}"),
            ("真气通脉 (vein guide)", "vein guide"),
            ("走火恢复 (vein recover)", "vein recover"),
        ],
    ),
    (
        "特技系统",
        "指令 special <skill>；激活 special + <skill>；升级 special upgrade <skill>。特技点：1.5m/10m/过劫难各3500点，100m 5000点等。"
        "职业特技 8 种：青龙 intellect/perceive，玄武 ironskin/corporeity，白虎 might/effectiveness，朱雀 agile/chainless。",
        [
            ("特技列表 (special)", "special"),
            ("激活 (special +)", "special + {特技名}"),
            ("取消 (special -)", "special - {特技名}"),
            ("运用特技 (special x)", "special {特技名}"),
            ("查升级 (special query)", "special query {特技名}"),
            ("升级 (special upgrade)", "special upgrade {特技名}"),
        ],
    ),
    (
        "真气系统",
        "真气=护体膜，增强防御；hp 显示真气层数与消除%。condense 把气血/内力转真气；750级内功可 yun shield。"
        "真气可 set vigour_vein 1 通脉（走火率减半）。",
        [
            ("真气 (condense)", "condense"),
            ("真气护体 (yun shield)", "yun shield"),
            ("真气通脉开关 (set vigour_vein 1)", "set vigour_vein 1"),
            ("关闭真气通脉 (set vigour_vein 0)", "set vigour_vein 0"),
        ],
    ),
    (
        "武学之道（研修）",
        "开启 set cultivate 1 后任务经验按比例存修行点；cultivate -r 看报告；闭关取点 / 完善 / 研修。"
        "研修突破 1800 级技能上限；biguan -qb 查询消耗与成功率。",
        [
            ("开启武道 (set cultivate 1)", "set cultivate 1"),
            ("报告 (cultivate -r)", "cultivate -r"),
            ("闭关 (cultivate -b)", "cultivate -b"),
            ("完善武功 (cultivate -i)", "cultivate -i {武功}"),
            ("研修缮 (biguan -qb)", "biguan -qb {技能}"),
            ("研修突破 (biguan -s)", "biguan -s {技能}"),
        ],
    ),
]


class XiuxianDock(QWidget):
    """修炼辅助 dock：修炼/天赋/技能/经脉/特技/真气/武道 一键命令。"""

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setMinimumWidth(230)
        self.setWindowTitle("辅助修炼")

        inner = QWidget(self)
        il = QVBoxLayout(inner)
        il.setContentsMargins(4, 4, 4, 4)
        il.setSpacing(2)

        # 定位按钮：gif 一键发命令
        quick = QHBoxLayout()
        btn_ms = QPushButton("打坐")
        btn_ms.clicked.connect(lambda: self._fire("dazuo max"))
        btn_tuna = QPushButton("吐纳")
        btn_tuna.clicked.connect(lambda: self._fire("tuna max"))
        btn_r = QPushButton("报告")
        btn_r.clicked.connect(lambda: self._fire("cultivate -r"))
        quick.addWidget(btn_ms)
        quick.addWidget(btn_tuna)
        quick.addWidget(btn_r)
        il.addLayout(quick)

        for title, hint, items in _GROUPS:
            hdr = QLabel(title)
            hdr.setStyleSheet("font-weight:bold; margin-top:4px;")
            hdr.setToolTip(hint)
            il.addWidget(hdr)
            grid = QGridLayout()
            grid.setSpacing(2)
            for i, (label, template) in enumerate(items):
                b = QPushButton(label)
                b.setToolTip(f"发送: {template}")
                b.clicked.connect(lambda _=False, t=template: self._fire(t))
                b.setAutoExclusive(False)
                grid.addWidget(b, i // 2, i % 2)
            il.addLayout(grid)

        il.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)

        if session is not None:
            self.bind(session)

    def bind(self, session) -> None:
        self.session = session

    def _fire(self, template: str) -> None:
        if self.session is None:
            return
        # 收集占位符
        placeholders = re.findall(r"\{([^}]+)\}", template)
        text = template
        for p in placeholders:
            val, ok = QInputDialog.getText(
                self, "命令参数", f"替换 «{p}»：", text=str(p)
            )
            if not ok or not val.strip():
                return
            text = text.replace("{" + p + "}", val.strip())
        if text.strip():
            self.session.send(text)