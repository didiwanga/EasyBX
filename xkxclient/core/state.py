from __future__ import annotations

import re

from xkxclient.core.gmcp import clean_ansi, fix_double

_HP_RE = re.compile(r"[（(【?【](\S+)[】]?\s*[:：]?\s*[^\d]*(\d+)\s*/\s*(\d+)")


class CharacterState:
    """人物状态（wiki B7/C-GMCP C3）。GMCP.Status + hp/score 文本解析。

    除基础属性外，还聚合 GMCP 附加数据：
    - enemy 敌人快照（GMCP.Combat 全字段）
    - buffs buff 列表（GMCP.Buff）
    - fighter_spirit 战意 / vigour 真气 / yuan 真元（GMCP.Status）
    """

    def __init__(self) -> None:
        self.name = ""
        self.id = ""
        self.title = ""
        self.family = ""
        self.level = 0
        self.qi = 0
        self.max_qi = 0
        self.jing = 0
        self.max_jing = 0
        self.jingli = 0
        self.max_jingli = 0
        self.neili = 0
        self.max_neili = 0
        self.eff_qi = 0
        self.eff_jing = 0
        self.food = 0
        self.water = 0
        self.combat_exp = 0
        self.potential = 0
        self.str = 0
        self.dex = 0
        self.int = 0
        self.con = 0
        self.per = 0
        self.attrs: dict = {}
        # ---- GMCP.Status 附加 ----
        self.fighter_spirit = 0.0      # 战意（气势）
        self.vigour = 0.0              # 真气
        self.yuan = 0.0                # 真元
        # ---- GMCP.Combat 快照 ----
        self.enemy: dict = {}          # 敌人信息（enemy_name/qi_pct/eff_qi_pct/qi_damage/…）
        self.perform_cd: dict = {}     # perform_id -> 剩余 CD 秒（服务端通知）
        self.in_combat = False
        # ---- GMCP.Buff ----
        self.buffs: list[dict] = []    # [{name, remaining, terminated, …}]

    def update_from_gmcp_status(self, data: dict) -> bool:
        changed = False
        # 身份字段：GMCP.Status 的 name/id 在战斗时是「当前敌人」（id 带 #oid），
        # 只有在 id 不含 `#`（自身身份）时才允许覆盖玩家名，避免状态栏被敌人名污染。
        ident = data.get("id")
        if isinstance(ident, str) and "#" in ident:
            data = {k: v for k, v in data.items() if k not in ("name", "id")}
        mapping = {
            "name": "name", "id": "id", "title": "title", "level": "level",
            "qi": "qi", "max_qi": "max_qi", "jing": "jing", "max_jing": "max_jing",
            "jingli": "jingli", "max_jingli": "max_jingli",
            "neili": "neili", "max_neili": "max_neili",
            "eff_qi": "eff_qi", "eff_jing": "eff_jing",
            "food": "food", "water": "water", "combat_exp": "combat_exp",
            "potential": "potential",
        }
        for key, attr in mapping.items():
            if key in data:
                val = data[key]
                if isinstance(val, str):
                    val = clean_ansi(val)
                current = getattr(self, attr)
                cmp_val = val
                # 数值型字段：字符串与已存数值比较前归一化，避免同值重复误报 changed
                if attr in ("qi", "max_qi", "jing", "max_jing", "jingli", "max_jingli",
                            "neili", "max_neili", "eff_qi", "eff_jing", "food", "water",
                            "combat_exp", "potential"):
                    try:
                        if isinstance(cmp_val, str) and cmp_val != "":
                            cmp_val = float(cmp_val)
                        elif isinstance(current, (int, float)):
                            cmp_val = float(cmp_val) if cmp_val not in (None, "") else cmp_val
                    except (TypeError, ValueError):
                        pass
                if cmp_val != current:
                    setattr(self, attr, val)
                    changed = True
        # GMCP 数值字段可能为字符串，统一转 float（供 statusdock 除法使用）
        for attr in ("qi", "max_qi", "jing", "max_jing", "jingli", "max_jingli",
                     "neili", "max_neili", "eff_qi", "eff_jing", "food", "water",
                     "combat_exp", "potential"):
            v = getattr(self, attr, None)
            if v in (None, ""):
                continue
            try:
                if not isinstance(v, (int, float)):
                    setattr(self, attr, float(v))
                    changed = True
            except (TypeError, ValueError):
                pass
        for k in ("str", "dex", "int", "con", "per"):
            if k in data and data[k] != getattr(self, k):
                val = data[k]
                try:
                    if isinstance(val, str):
                        val = clean_ansi(val)
                        val = int(val)
                except (TypeError, ValueError):
                    pass
                setattr(self, k, val)
                changed = True
        fam = data.get("family/family_name")
        if fam is not None:
            fam = clean_ansi(fam)
            if fam and fam != self.family:
                self.family = fam
                changed = True
        # 战意/真气/真元（GMCP.Status 扩展字段）
        for key, attr in (("fighter_spirit", "fighter_spirit"),
                          ("vigour", "vigour"), ("yuan", "yuan")):
            if key in data:
                try:
                    val = float(data[key])
                except (TypeError, ValueError):
                    continue
                if abs(val - getattr(self, attr)) > 1e-6:
                    setattr(self, attr, val)
                    changed = True
        return changed

    # ---- GMCP.Combat 快照 ----
    def update_enemy(self, data: dict) -> bool:
        """合并 GMCP.Combat 敌人字段到 self.enemy。返回是否有变化。"""
        if not isinstance(data, dict):
            return False
        cur = dict(self.enemy)
        changed = False
        for key, val in data.items():
            if isinstance(val, str):
                val = clean_ansi(val)
            if cur.get(key) != val:
                cur[key] = val
                changed = True
        if changed:
            self.enemy = cur
        # combat 进出信号（GMCP 值可能为字符串 "true"/"false"）
        if "enemy_in" in data:
            v = str(data.get("enemy_in")).lower()
            self.in_combat = v == "true" or self.in_combat
        if "enemy_out" in data and str(data.get("enemy_out")).lower() == "true":
            self.in_combat = False
        return changed

    def update_perform_cd(self, perform_id, data: dict) -> None:
        """GMCP.Combat.perform_* 通知：记录某绝招进入 CD（服务端给的 para/字段）。"""
        if not perform_id:
            return
        self.perform_cd[perform_id] = data

    # ---- GMCP.Buff ----
    def update_buffs(self, data) -> bool:
        """聚合 GMCP.Buff 列表。data 可为单一 buff dict 或 buff 数组。"""
        items = data if isinstance(data, list) else [data]
        raw: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = clean_ansi(str(it.get("name") or it.get("type") or ""))
            if not name:
                continue
            raw.append({
                "name": name,
                "type": it.get("type", ""),
                "is_end": bool(it.get("is_end")),
                "terminated": bool(it.get("terminated")),
                "last_time": it.get("last_time"),
                "last_inc": it.get("last_inc"),
                "remaining": it.get("last_time"),
                "data": it,
            })
        if raw != self.buffs:
            self.buffs = raw
            return True
        return False

    def update_from_score_text(self, text: str) -> None:
        text = clean_ansi(text)
        m = re.search(r"([\u4e00-\u9fff·]+)\(([A-Za-z0-9]+)\)", text)
        if m:
            self.name, self.id = m.group(1), m.group(2)
        m = re.search(r"(膂力|悟性|根骨|身法|福缘|容貌|灵性|胆识)(?:：|:)\[*\s*(\d+)", text)
        if not m:
            for label, attr in (("膂力", "str"), ("悟性", "int"), ("根骨", "con"),
                                ("身法", "dex"), ("容貌", "per")):
                m2 = re.search(f"{label}(?:：|:)\\s*\\[?\\s*(\\d+)", text)
                if m2:
                    setattr(self, attr, int(m2.group(1)))

    def update_from_hp_text(self, text: str) -> None:
        t = clean_ansi(text)
        for label, attr in (("气血", "qi"), ("精神", "jing"), ("精力", "jingli"),
                            ("内力", "neili"), ("食物", "food"), ("饮水", "water")):
            m = re.search(f"[【{label}】]?{label}\\s*[:：]?.*?(\\d+)\\s*/\\s*(\\d+)", t)
            if m:
                setattr(self, attr, int(m.group(1)))
                setattr(self, "max_" + attr, int(m.group(2)))