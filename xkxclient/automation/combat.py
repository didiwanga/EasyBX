from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from xkxclient.core.config import ConfigManager


def _is_truthy(val) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    return str(val).strip().lower() == "true"


class CombatEngine(QObject):
    """自动战斗辅助引擎（wiki 战斗系统 + busy类/物理/化学 + CD绑定）。

    - 战前准备：jifa/bei/wbei/jiali/wield 一键下发。
    - 自动轮转：按配置的动作序列在战斗中逐条执行，每条独立冷却（CD 绑定）。
    - 防御回血：气血低于阈值时暂停进攻并执行恢复命令，防止暴毙。
    - 状态感知：依据 GMCP.Status(combat/busy) 与 GMCP.Combat(enemy) 驱动。
    """

    msg_signal = pyqtSignal(str)

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.account = session.account_id
        self.enabled = False
        self.fighting = False
        self.busy = False
        self.enemy = ""
        self.enemy_hp = 0
        self.enemy_qi_pct = 0.0
        self.enemy_eff_qi_pct = 0.0
        self.msg = ""
        self.actions: list[dict] = []
        self.rotation: list[dict] = []
        self._last_cd_time: dict[str, float] = {}
        self._defense_until = 0.0
        self._global_gap = 0.6
        self._last_send = 0.0
        self._last_fight_at = 0.0
        self._cfg: dict = {}
        # L1：实时状态（面板轮询用）
        self.current_step = ""            # 当前执行的招式名
        self.current_cmd = ""             # 当前执行的完整命令
        self.step_started_at = 0.0        # 当前招式开始时间
        self.total_damage = 0             # 累计伤害
        self.fight_count = 0              # 累计战斗次数
        self.last_step_at = 0.0

        self.buff_watch: list[dict] = []   # [{name, cmd, cooldown}]
        self._buff_last_cast: dict[str, float] = {}

        self._load_buff_watch()
        self._load_cfg()
        self._rebuild_actions()

        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

        self.bus = session.app.bus
        self.bus.subscribe("state.changed", self._on_state)
        self.bus.subscribe("GMCP.Combat", self._on_combat)
        self.bus.subscribe("state.combat", self._on_enemy)
        self.bus.subscribe("state.buffs", self._on_buffs)

    # ---- 配置 ----
    def _load_cfg(self) -> None:
        cfg = ConfigManager.instance()
        self._cfg = dict(cfg.get(f"combat.{self.account}") or {})
        self.enabled = bool(self._cfg.get("enabled", False))
        self.rotation = list(self._cfg.get("rotation") or [])
        self._migrate_cd_units()

    def _migrate_cd_units(self) -> None:
        """旧版冷却为秒（0-300），现统一为毫秒：把看起来像秒的旧值 ×1000 迁移。"""
        changed = False
        for item in self.rotation:
            if isinstance(item, dict) and item.get("cd") and item["cd"] <= 300:
                item["cd"] = int(item["cd"]) * 1000
                changed = True
        for w in self.buff_watch:
            if isinstance(w, dict) and w.get("cooldown") and w["cooldown"] <= 300:
                w["cooldown"] = int(w["cooldown"]) * 1000
                changed = True
        if changed:
            self.save_cfg()

    def set_rotation(self, rotation: list[dict]) -> None:
        self.rotation = list(rotation or [])
        self._rebuild_actions()
        self.save_cfg()

    def _load_buff_watch(self) -> None:
        cfg = ConfigManager.instance()
        self.buff_watch = list((cfg.get(f"combat.{self.account}") or {}).get("buff_watch") or [])

    def save_cfg(self) -> None:
        pieces = {"enabled": self.enabled, "rotation": self.rotation,
                  "buff_watch": self.buff_watch}
        self._cfg.update(pieces)
        ConfigManager.instance().set(f"combat.{self.account}", dict(self._cfg))

    def set_buff_watch(self, items: list[dict]) -> None:
        self.buff_watch = [dict(i) for i in items if isinstance(i, dict) and i.get("name") and i.get("cmd")]
        self.save_cfg()

    def _rebuild_actions(self) -> None:
        acts = []
        for item in self.rotation:
            if not isinstance(item, dict):
                continue
            cmd = str(item.get("cmd") or "").strip()
            if not cmd:
                continue
            acts.append({
                "cmd": cmd,
                "cd": max(0.0, float(item.get("cd") or 0) / 1000.0),
                "min_qi": int(item.get("min_qi") or 0),
                "desc": str(item.get("desc") or cmd),
            })
        self.actions = acts

    # ---- 战前准备 ----
    def prep_cfg(self, jifa: dict | None = None, bei: list | None = None,
                 wbei: list | None = None, wield: str = "", jiali: str = "") -> None:
        prep = self._cfg.get("prep")
        if not isinstance(prep, dict):
            prep = {}
        if jifa is not None:
            prep["jifa"] = jifa
        if bei is not None:
            prep["bei"] = bei
        if wbei is not None:
            prep["wbei"] = wbei
        if wield:
            prep["wield"] = wield
        if jiali != "":
            prep["jiali"] = jiali
        self._cfg["prep"] = prep
        self.save_cfg()

    def do_prep(self) -> None:
        """一键下发战前准备命令序列（先清互备→jifa→wield→bei/wbei→jiali）。"""
        p = self._cfg.get("prep") or {}
        if not isinstance(p, dict):
            return
        cmds = ["wbei none", "bei none"]
        jifa = p.get("jifa") or {}
        if isinstance(jifa, dict):
            for base, sf in jifa.items():
                if str(base).strip() and str(sf).strip():
                    cmds.append(f"jifa {base} {sf}")
        if p.get("wield"):
            cmds.append(f"wield {p['wield']}")
        bei = p.get("bei") or []
        if bei:
            cmds.append("bei " + " ".join(str(b) for b in bei))
        wbei = p.get("wbei") or []
        if wbei:
            cmds.append("wbei " + " ".join(str(b) for b in wbei))
        jiali = p.get("jiali")
        if jiali is not None and str(jiali) != "":
            cmds.append(f"jiali {jiali}")
        self._send_sequence(cmds, "战前准备")

    # ---- 运行 ----
    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        self._last_cd_time = {}
        self._defense_until = 0.0
        self.save_cfg()
        self._log(f"自动战斗{'开始' if on else '停止'}")

    def _on_state(self, payload: dict) -> None:
        if payload.get("account") != self.account:
            return
        st = payload.get("state")
        if st is None:
            return
        self.fighting = _is_truthy(getattr(st, "fighting", False))
        self.busy = _is_truthy(getattr(st, "busy", False))

    def _on_combat(self, payload: dict) -> None:
        if payload.get("account") != self.account:
            return
        data = payload.get("data") or {}
        # 北侠 GMCP.Combat 的真实载荷可能是 list（风暴期重复触发），不是字典
        if isinstance(data, list):
            data = {}
        self.enemy = str(data.get("enemy_name") or data.get("name") or self.enemy or "")
        dmg = data.get("qi_damage")
        if dmg is not None:
            try:
                self.enemy_hp = int(dmg)
                self.total_damage += int(dmg)
            except (TypeError, ValueError):
                pass
        # L1：敌人进场 = 立即进入战斗，不等 500ms 轮询，立刻发一轮攻击
        if _is_truthy(data.get("enemy_in")):
            self._enter_fight(instant=True)

    def _on_enemy(self, payload: dict) -> None:
        if payload.get("account") != self.account:
            return
        enemy = payload.get("enemy") or {}
        if not enemy:
            return
        self.enemy = str(enemy.get("enemy_name") or enemy.get("name") or self.enemy or "")
        for key in ("qi_pct", "eff_qi_pct"):
            val = enemy.get(key)
            if val is not None:
                try:
                    if key == "qi_pct":
                        self.enemy_qi_pct = float(val)
                    else:
                        self.enemy_eff_qi_pct = float(val)
                except (TypeError, ValueError):
                    pass
        # 敌人进场 / 出场由 event 驱动，秒进秒出
        if _is_truthy(enemy.get("enemy_in")):
            self._enter_fight()
        if _is_truthy(enemy.get("enemy_out")):
            self._exit_fight()

    def _enter_fight(self, instant: bool = False) -> None:
        """进入战斗：立即标记 fighting，可选立刻发一轮攻击。"""
        if not self.fighting:
            self.fighting = True
            self.current_step = ""
            self.step_started_at = 0.0
            self._log(f"进入战斗: {self.enemy or '敌人'}")
        self._last_fight_at = time.time()
        if instant and self.enabled:
            # 覆盖节流：战斗开始立即出手（throttle 仍限频保命）
            now = time.time()
            self._last_fight_at = now
            self._last_send = now - self._global_gap
            self._do_rotation(now)

    def _exit_fight(self) -> None:
        """战斗结束：清状态，累计一次战斗。"""
        if self.fighting:
            self.fight_count += 1
            self._log(f"战斗结束 #{self.fight_count}: {self.enemy or '未知敌人'}")
        self.fighting = False
        self.enemy_qi_pct = 0.0
        self.enemy_eff_qi_pct = 0.0
        self.current_step = ""
        self.current_cmd = ""
        self.last_step_at = 0.0
        self._last_fight_at = 0.0

    def _on_buffs(self, payload: dict) -> None:
        if payload.get("account") != self.account:
            return
        if not self.enabled or not self.buff_watch:
            return
        active_names = {str(b.get("name")) for b in (payload.get("buffs") or [])
                        if not b.get("terminated") and not b.get("is_end")}
        now = time.time()
        for w in self.buff_watch:
            name = str(w.get("name") or "")
            if not name or any(name in a or a in name for a in active_names):
                continue
            cd = float(w.get("cooldown") or 0) / 1000.0
            if now - self._buff_last_cast.get(name, 0.0) >= cd:
                self._buff_last_cast[name] = now
                self.session.send_auto(str(w.get("cmd") or ""))
                self._log(f"续buff: {name}")

    def _on_tick(self) -> None:
        if not self.enabled:
            return
        if not (self.session.connected and self.session.logged_in):
            return
        st = self.session.state
        self.fighting = _is_truthy(getattr(st, "fighting", False))
        self.busy = _is_truthy(getattr(st, "busy", False))
        if self.busy:
            return
        now = time.time()
        low = self._low_qi()
        if low > 0 and st.max_qi:
            pct = st.qi / st.max_qi * 100
            if pct <= low:
                if now >= self._defense_until:
                    self._run_defense()
                    self._defense_until = now + 5.0
                return
        if not self.fighting:
            if self._last_fight_at and now - self._last_fight_at > 3:
                self._log(f"战斗结束: {self.enemy or '未知敌人'}")
                self._last_fight_at = 0.0
            return
        self._last_fight_at = now
        if now - self._last_send >= self._global_gap:
            self._do_rotation(now)

    def _do_rotation(self, now: float) -> None:
        st = self.session.state
        # 敌人已空/低残：可能下一击结束战斗，避免在黑区浪费 busy 节拍
        if self.enemy_eff_qi_pct and self.enemy_eff_qi_pct <= 0.0:
            return
        for act in self.actions:
            if now - self._last_cd_time.get(act["cmd"], 0.0) < act["cd"]:
                continue
            min_qi = act.get("min_qi") or 0
            if min_qi > 0 and st.max_qi:
                if st.qi / st.max_qi * 100 < min_qi:
                    continue
            self._send_sequence([act["cmd"]], act.get("desc") or "pfm")
            self._last_cd_time[act["cmd"]] = now
            self._last_send = now
            # L1：记录当前步骤供面板显示
            self.current_step = act.get("desc") or act["cmd"]
            self.current_cmd = act["cmd"]
            self.step_started_at = now
            self.last_step_at = now
            return

    def _run_defense(self) -> None:
        d = self._cfg.get("defense") or {}
        rc = d.get("recover_cmds")
        if not rc:
            return
        cmds = rc if isinstance(rc, list) else [rc]
        self._send_sequence([str(c) for c in cmds if str(c).strip()], "低血防御")
        self._log("气血低于设定，防御/恢复中")

    def _low_qi(self) -> int:
        d = self._cfg.get("defense") or {}
        try:
            return int(d.get("low_qi_pct") or 0)
        except (TypeError, ValueError):
            return 0

    def save_defense(self, low_qi_pct: int, recover_cmds: list[str]) -> None:
        self._cfg["defense"] = {"low_qi_pct": int(low_qi_pct), "recover_cmds": list(recover_cmds)}
        self.save_cfg()

    # ---- 工具 ----
    def _send_sequence(self, cmds: list[str], label: str = "") -> None:
        for c in cmds:
            c = str(c).strip()
            if c:
                self.session.send_auto(c)
        if label:
            self._log(label)

    def _log(self, text: str) -> None:
        self.msg = text
        self.msg_signal.emit(text)
        self.bus.publish("combat.msg", account=self.account, text=text)

    def close(self) -> None:
        self._tick.stop()