from __future__ import annotations

from PyQt6.QtWidgets import (
    QFormLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


def _clamp(ratio: float) -> int:
    """B7 clamp：cur>=max 恒 100%；禁止 >100%。"""
    if ratio is None:
        return 0
    if ratio >= 1.0:
        return 100
    return max(0, min(100, int(ratio * 100)))


def _int(v) -> int:
    """数值显示用整数（GMCP 可能是 float('1000.0') 或字符串）。"""
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return int(v) if v else 0


def _pct(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


class StateDock(QWidget):
    """C-GMCP 状态停靠：气血/精神/精力条 + 敌人快照 + 战意/真气/真元 + buff 列表。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(220)
        self.name_label = QLabel("—")
        self.meta_label = QLabel("")
        self.qi_bar = QProgressBar(); self.qi_bar.setRange(0, 100)
        self.jing_bar = QProgressBar(); self.jing_bar.setRange(0, 100)
        self.jl_bar = QProgressBar(); self.jl_bar.setRange(0, 100)
        self.nl_bar = QProgressBar(); self.nl_bar.setRange(0, 100)
        self.food_bar = QProgressBar(); self.food_bar.setRange(0, 100)
        self.water_bar = QProgressBar(); self.water_bar.setRange(0, 100)
        self.enemy_bar = QProgressBar(); self.enemy_bar.setRange(0, 100)
        self.enemy_bar.setFormat("敌人 %v%")
        self.enemy_label = QLabel("")
        self.spirit_label = QLabel("")   # 战意 / 真气 / 真元
        self.attr_label = QLabel("")
        self.attr_label.setWordWrap(True)
        self.buff_label = QLabel("")
        self.buff_label.setWordWrap(True)

        rows = QFormLayout()
        rows.addRow("气血", self.qi_bar)
        rows.addRow("内力", self.nl_bar)
        rows.addRow("精神", self.jing_bar)
        rows.addRow("精力", self.jl_bar)
        rows.addRow("食物", self.food_bar)
        rows.addRow("饮水", self.water_bar)
        rows.addRow("属性", self.attr_label)
        rows.addRow("敌人", self.enemy_bar)
        rows.addRow("", self.enemy_label)
        rows.addRow("战况", self.spirit_label)
        rows.addRow("Buff", self.buff_label)

        inner = QWidget(self)
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.addWidget(self.name_label)
        il.addWidget(self.meta_label)
        il.addLayout(rows)
        il.addWidget(self.attr_label)
        il.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)

    def update_state(self, st) -> None:
        name = st.name or "—"
        title = st.title or ""
        fam = st.family or ""
        self.name_label.setText(f"{name} {title} Lv{st.level or '?'}".strip())
        # 人物状态：🔴战斗 > 🟡忙碌 > 🟢空闲（GMCP fighting/in_combat/busy 三态可区分）
        busy = getattr(st, "busy", None)
        fighting = getattr(st, "fighting", None)
        in_combat = getattr(st, "in_combat", None)
        fighting_on = (fighting is not None and str(fighting).lower() == "true")
        if fighting_on or in_combat:
            state_text = "🔴 战斗"
        elif busy is not None and str(busy).lower() == "true":
            state_text = "🟡 忙碌"
        else:
            state_text = "🟢 空闲"
        self.meta_label.setText(f"{fam}  exp {_int(st.combat_exp)}  {state_text}")
        self.qi_bar.setValue(_clamp(st.qi / st.max_qi if st.max_qi else 0))
        self.qi_bar.setFormat(f"{_int(st.qi)}/{_int(st.max_qi)}")
        self.nl_bar.setValue(_clamp(st.neili / st.max_neili if st.max_neili else 0))
        self.nl_bar.setFormat(f"{_int(st.neili)}/{_int(st.max_neili)}")
        self.jing_bar.setValue(_clamp(st.jing / st.max_jing if st.max_jing else 0))
        self.jing_bar.setFormat(f"{_int(st.jing)}/{_int(st.max_jing)}")
        self.jl_bar.setValue(_clamp(st.jingli / st.max_jingli if st.max_jingli else 0))
        self.jl_bar.setFormat(f"{_int(st.jingli)}/{_int(st.max_jingli)}")
        self.food_bar.setValue(_clamp(st.food / 300 if st.food else 0))
        self.food_bar.setFormat(f"{_int(st.food)}/300")
        self.water_bar.setValue(_clamp(st.water / 300 if st.water else 0))
        self.water_bar.setFormat(f"{_int(st.water)}/300")

        # 有效气血/精（中毒/受伤），跟上限并列显示
        eff_qi = getattr(st, "eff_qi", 0) or 0
        eff_jing = getattr(st, "eff_jing", 0) or 0
        qi_fmt = self.qi_bar.format()
        jing_fmt = self.jing_bar.format()
        if eff_qi:
            self.qi_bar.setFormat(f"{qi_fmt} (有效{eff_qi})")
        if eff_jing:
            self.jing_bar.setFormat(f"{jing_fmt} (有效{eff_jing})")

        # 四维属性 + 潜能/经验（GMCP.Status 根级 str/dex/int/con/per）
        parts = []
        for label, val in (("力", st.str), ("身", st.dex), ("悟", st.int),
                           ("根", st.con), ("容", st.per)):
            if val is not None:
                parts.append(f"{label}{val}")
        if getattr(st, "potential", None):
            parts.append(f"潜{_int(st.potential)}")
        self.attr_label.setText("  ".join(parts) if parts else "")

        spirit = getattr(st, "fighter_spirit", 0) or 0
        vig = getattr(st, "vigour", 0) or 0
        yuan = getattr(st, "yuan", 0) or 0
        parts = [f"战意{spirit:.0f}"]
        if vig:
            parts.append(f"真{vig:.0f}")
        if yuan:
            parts.append(f"元{yuan:.0f}")
        self.spirit_label.setText(" ".join(parts))

        buffs = getattr(st, "buffs", None)
        if buffs:
            texts = []
            for b in buffs:
                name = b.get("name", "")
                rem = b.get("remaining")
                if b.get("terminated"):
                    continue
                if rem is not None:
                    texts.append(f"{name}:{rem}")
                else:
                    texts.append(str(name))
            self.buff_label.setText("\n".join(texts) if texts else "")
        else:
            self.buff_label.setText("")

    def update_combat(self, data) -> None:
        # 北侠战斗风暴期 data 可能是 list（多条 Combat 事件），归一为 dict
        if isinstance(data, list):
            data = next((it for it in data if isinstance(it, dict)), {})
        if not isinstance(data, dict):
            data = {}
        en = data.get("enemy_name") or data.get("name") or ""
        dmg = data.get("qi_damage")
        pct = data.get("qi_pct") or data.get("eff_qi_pct")
        bits = []
        if en:
            bits.append(str(en))
        if pct is not None:
            self.enemy_bar.setValue(_clamp(_pct(pct) / 100))
        if dmg is not None:
            bits.append(f"伤害{dmg}")
        self.enemy_label.setText(" ".join(bits))
        if not bits:
            self.enemy_bar.setValue(0)

    def update_enemy(self, enemy) -> None:
        """GMCP.Combat 全字段更新（enemy/eff_qi_pct 等）。"""
        if isinstance(enemy, list):
            enemy = next((it for it in enemy if isinstance(it, dict)), {})
        if not isinstance(enemy, dict) or not enemy:
            return
        en = enemy.get("enemy_name") or enemy.get("name") or ""
        pct = enemy.get("qi_pct")
        eff = enemy.get("eff_qi_pct")
        dmg = enemy.get("qi_damage")
        bits = []
        if en:
            bits.append(str(en))
        if pct is not None:
            self.enemy_bar.setValue(_clamp(_pct(pct) / 100))
        elif eff is not None:
            self.enemy_bar.setValue(_clamp(_pct(eff) / 100))
        else:
            self.enemy_bar.setValue(0)
        if eff is not None:
            self.enemy_bar.setFormat(f"敌人 {_pct(eff):.0f}%")
        elif pct is not None:
            self.enemy_bar.setFormat(f"敌人 {_pct(pct):.0f}%")
        else:
            self.enemy_bar.setFormat("敌人")
        if dmg is not None:
            bits.append(f"伤害{dmg}")
        self.enemy_label.setText(" ".join(bits) if bits else "")

    def refresh_buffs(self, buffs: list) -> None:
        if not buffs:
            self.buff_label.setText("")
            return
        texts = []
        for b in buffs:
            name = b.get("name", "")
            rem = b.get("remaining")
            if b.get("terminated") or b.get("is_end"):
                continue
            if rem is not None:
                texts.append(f"{name}: {rem}")
            else:
                texts.append(str(name))
        self.buff_label.setText("\n".join(texts) if texts else "")

    def set_enemy(self, text: str) -> None:
        self.enemy_label.setText(text)