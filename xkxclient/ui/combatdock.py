from __future__ import annotations

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from xkxclient.core.resources import PROJECT_ROOT

import json as _json

_PFM_PRESETS = []


def _load_menpai_presets() -> dict:
    path = PROJECT_ROOT / "resources" / "menpai_presets.json"
    try:
        with open(path, encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, ValueError):
        return {}


_MENPAI_PRESETS = _load_menpai_presets()


class _StepDialog(QWidget):
    def __init__(self, engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.cmd_ed = QLineEdit()
        self.cd_sp = QSpinBox(); self.cd_sp.setRange(0, 300000); self.cd_sp.setSuffix(" ms")
        self.qi_sp = QSpinBox(); self.qi_sp.setRange(0, 100); self.qi_sp.setSuffix(" %")
        form = QFormLayout(self)
        form.addRow("命令", self.cmd_ed)
        form.addRow("冷却", self.cd_sp)
        form.addRow("气血门槛", self.qi_sp)


class CombatAssistDock(QWidget):
    """自动战斗辅助 dock：战前准备 + 自动轮转 + 防御 + 状态。"""

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self._engine = None
        self._refresh_timer = None
        self.setMinimumWidth(240)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(QLabel("自动战斗辅助"))

        # 主开关
        self.enable_cb = QCheckBox("启用自动战斗")
        self.enable_cb.toggled.connect(self._toggle_enabled)
        lay.addWidget(self.enable_cb)

        # 状态行
        self.status = QLabel("未启用")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        # 实时统计（L1）：敌人 / 当前招式 / 累计伤害 / 次数
        self.stats = QLabel("")
        self.stats.setWordWrap(True)
        self.stats.setStyleSheet("color:#5a7ad1; font-weight:bold;")
        lay.addWidget(self.stats)

        # 战前准备
        prep = QWidget(self)
        pv = QVBoxLayout(prep)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.addWidget(QLabel("战前准备"))
        self.jifa_ed = QLineEdit(); self.jifa_ed.setPlaceholderText("force beiming;sword taiji-jian")
        self.bei_ed = QLineEdit(); self.bei_ed.setPlaceholderText("bei 招式（空格分隔）")
        self.wbei_ed = QLineEdit(); self.wbei_ed.setPlaceholderText("wbei 招式（空格分隔）")
        row = QHBoxLayout()
        self.wield_ed = QLineEdit(); self.wield_ed.setPlaceholderText("武器")
        self.jiali_ed = QLineEdit(); self.jiali_ed.setPlaceholderText("加力")
        row.addWidget(self.wield_ed)
        row.addWidget(self.jiali_ed)
        prep_btn = QPushButton("一键备战")
        prep_btn.clicked.connect(self._on_prep)
        pv.addWidget(self.jifa_ed)
        pv.addWidget(self.bei_ed)
        pv.addWidget(self.wbei_ed)
        pv.addLayout(row)
        pv.addWidget(prep_btn)
        lay.addWidget(prep)

        # 自动轮转
        rot = QWidget(self)
        rv = QVBoxLayout(rot)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("自动招式轮转 (CD 绑定)"))
        preset_row = QHBoxLayout()
        self.menpai_cb = QComboBox()
        self.menpai_cb.addItem("门派…", "")
        for key in _MENPAI_PRESETS:
            self.menpai_cb.addItem(_MENPAI_PRESETS[key].get("name", key), key)
        self.preset_cb = QComboBox()
        self.preset_cb.addItem("— 选择该门派方案 —", None)
        self.menpai_cb.currentIndexChanged.connect(self._on_menpai_change)
        apply_preset_btn = QPushButton("应用方案")
        apply_preset_btn.clicked.connect(self._on_apply_preset)
        preset_row.addWidget(self.menpai_cb)
        preset_row.addWidget(self.preset_cb, 1)
        preset_row.addWidget(apply_preset_btn)
        rv.addLayout(preset_row)
        self.step_list = QListWidget()
        self.step_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        # 让列表保持至少两行的自然高度（不覆盖其内在 minimumSizeHint）
        self.step_list.setMinimumHeight(72)
        rv.addWidget(self.step_list, 1)
        btns = QHBoxLayout()
        add_btn = QPushButton("添加"); add_btn.clicked.connect(self._on_add)
        del_btn = QPushButton("删除"); del_btn.clicked.connect(self._on_del)
        edit_btn = QPushButton("编辑"); edit_btn.clicked.connect(self._on_edit)
        btns.addWidget(add_btn); btns.addWidget(edit_btn); btns.addWidget(del_btn)
        rv.addLayout(btns)
        lay.addWidget(rot, 1)

        # 防御
        def_ = QWidget(self)
        dv = QHBoxLayout(def_)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.addWidget(QLabel("低血%"))
        self.def_qi_sp = QSpinBox(); self.def_qi_sp.setRange(0, 100)
        self.recover_ed = QLineEdit(); self.recover_ed.setPlaceholderText("yun recover;exert qi")
        dv.addWidget(self.def_qi_sp)
        dv.addWidget(self.recover_ed, 1)
        lay.addWidget(def_)

        # Buff 自动续约
        buf = QWidget(self)
        bv = QVBoxLayout(buf)
        bv.setContentsMargins(0, 0, 0, 0)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Buff 自动续约 (名:cmd)"))
        add_buff_btn = QPushButton("添加"); add_buff_btn.clicked.connect(self._on_add_buff)
        hdr.addWidget(add_buff_btn)
        bv.addLayout(hdr)
        self.buff_list = QListWidget()
        bv.addWidget(self.buff_list)
        lay.addWidget(buf)

        self.log_list = QListWidget()
        self.log_list.setMaximumHeight(110)
        lay.addWidget(self.log_list)

        self.bind(session)

    # ---- 绑定 ----
    def _unbind(self) -> None:
        """解绑旧引擎：断开信号 + 停定时器（bind 前必须调用，防重复累积）。"""
        old = getattr(self, "_engine", None)
        if old is not None:
            try:
                old.msg_signal.disconnect(self._on_msg)
            except (TypeError, RuntimeError):
                pass
        if self._refresh_timer is not None:
            self._refresh_timer.stop()

    def bind(self, session) -> None:
        self._unbind()
        self.session = session
        self._engine = getattr(session, "combat", None)
        if self._engine is None:
            return
        self.enable_cb.setChecked(self._engine.enabled)
        self._load_prep()
        self._reload_steps()
        self._reload_buffs()
        self.def_qi_sp.setValue(self._engine._low_qi())
        d = (self._engine._cfg.get("defense") or {})
        rc = d.get("recover_cmds")
        if rc:
            self.recover_ed.setText(";".join(rc) if isinstance(rc, list) else str(rc))
        self._engine.msg_signal.connect(self._on_msg)

        from PyQt6.QtCore import QTimer
        if self._refresh_timer is None:
            self._refresh_timer = QTimer(self)
            self._refresh_timer.setInterval(500)
            self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start()

    def _refresh(self) -> None:
        """每 500ms 刷新：步骤剩余 CD + 高亮当前执行招式 + 实时统计。"""
        self._reload_steps()
        e = self._engine
        if e.enabled:
            st = []
            if e.enemy:
                st.append(f"敌人: {e.enemy}")
            st.append("战斗中" if e.fighting else "待命中")
            cur = e.current_step or ""
            if cur:
                st.append(f"招式: {cur}")
            st.append(f"伤害: {e.total_damage}")
            st.append(f"C{ e.fight_count}")
            self.stats.setText("  |  ".join(st))
        else:
            self.stats.setText("")
        self.enable_cb.blockSignals(True)
        self.enable_cb.setChecked(e.enabled)
        self.enable_cb.blockSignals(False)

    def _on_msg(self, text: str) -> None:
        from PyQt6.QtCore import QDateTime
        ts = QDateTime.currentDateTime().toString("HH:mm:ss")
        self.log_list.addItem(f"[{ts}] {text}")
        while self.log_list.count() > 80:
            self.log_list.takeItem(0)
        self.log_list.scrollToBottom()
        self.status.setText(text)

    def _load_prep(self) -> None:
        p = (self._engine._cfg.get("prep") or {})
        jifa = p.get("jifa") or {}
        if isinstance(jifa, dict):
            self.jifa_ed.setText(";".join(f"{k} {v}" for k, v in jifa.items()))
        if p.get("bei"):
            self.bei_ed.setText(" ".join(str(b) for b in p["bei"]))
        if p.get("wbei"):
            self.wbei_ed.setText(" ".join(str(b) for b in p["wbei"]))
        self.wield_ed.setText(str(p.get("wield") or ""))
        self.jiali_ed.setText(str(p.get("jiali") or ""))

    def _reload_steps(self) -> None:
        import time as _time
        e = self._engine
        now = _time.time()
        # 记住用户选中的行，列表重建后会清空选中
        sel_row = self.step_list.currentRow()
        self.step_list.clear()
        for i, act in enumerate(e.rotation):
            cmd = act.get("cmd") or ""
            cd_ms = int(act.get("cd") or 0)
            qi = act.get("min_qi") or 0
            remaining_ms = max(0, int((e._last_cd_time.get(cmd, 0.0) + cd_ms / 1000.0 - now) * 1000))
            text = cmd
            if cd_ms:
                text += f"  [CD{cd_ms}ms"
                if remaining_ms > 0:
                    text += f" 剩{remaining_ms}ms"
                text += "]"
            if qi:
                text += f"  {qi}%"
            item = QListWidgetItem(text)
            # 高亮当前正在执行的招式（用背景色，不动用户选中）
            if e.current_cmd and e.current_cmd == cmd:
                item.setBackground(QColor("#3a5a8a"))
                item.setForeground(QColor("white"))
            elif remaining_ms > 0:
                item.setForeground(QColor("#888"))
            self.step_list.addItem(item)
        # 恢复用户选中行（旋转里第几行就选第几行）
        if 0 <= sel_row < self.step_list.count():
            self.step_list.setCurrentRow(sel_row)

    def _reload_buffs(self) -> None:
        self.buff_list.clear()
        for w in self._engine.buff_watch:
            cd = w.get("cooldown") or 0
            self.buff_list.addItem(QListWidgetItem(f"{w.get('name')} → {w.get('cmd')}  CD{cd}ms"))

    def _on_add_buff(self) -> None:
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle("Buff 续约")
        f = QFormLayout(dlg)
        name_ed = QLineEdit(); name_ed.setPlaceholderText("如 powerup")
        cmd_ed = QLineEdit(); cmd_ed.setPlaceholderText("如 yun powerup")
        cd_sp = QSpinBox(); cd_sp.setRange(0, 300000); cd_sp.setSuffix(" ms")
        f.addRow("Buff名", name_ed)
        f.addRow("命令", cmd_ed)
        f.addRow("冷却", cd_sp)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dlg)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        f.addRow(box)
        if dlg.exec() and name_ed.text().strip() and cmd_ed.text().strip():
            items = list(self._engine.buff_watch) + [{"name": name_ed.text().strip(),
                                                       "cmd": cmd_ed.text().strip(),
                                                       "cooldown": cd_sp.value()}]
            self._engine.set_buff_watch(items)
            self._reload_buffs()

    # ---- 槽 ----
    def _toggle_enabled(self, on: bool) -> None:
        if self._engine is not None:
            self._engine.set_enabled(on)

    def _on_prep(self) -> None:
        if self._engine is None:
            return
        jifa = {}
        for seg in self.jifa_ed.text().split(";"):
            seg = seg.strip()
            if not seg:
                continue
            parts = seg.split()
            if len(parts) >= 2:
                jifa[parts[0]] = " ".join(parts[1:])
        bei = self.bei_ed.text().split()
        wbei = self.wbei_ed.text().split()
        self._engine.prep_cfg(jifa=jifa, bei=bei, wbei=wbei,
                              wield=self.wield_ed.text().strip(),
                              jiali=self.jiali_ed.text().strip())
        self._engine.do_prep()

    def _step_form(self, item: dict | None) -> dict | None:
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle("招式")
        dlg.setMinimumWidth(340)
        f = QFormLayout(dlg)
        cmd_ed = QLineEdit()
        cd_sp = QSpinBox(); cd_sp.setRange(0, 300000); cd_sp.setSuffix(" ms")
        qi_sp = QSpinBox(); qi_sp.setRange(0, 100); qi_sp.setSuffix(" %")
        f.addRow("命令", cmd_ed)
        f.addRow("冷却", cd_sp)
        f.addRow("气血门槛%", qi_sp)
        if item:
            cmd_ed.setText(str(item.get("cmd") or ""))
            cd_sp.setValue(int(item.get("cd") or 0))
            qi_sp.setValue(int(item.get("min_qi") or 0))
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dlg)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        f.addRow(box)
        if dlg.exec() and cmd_ed.text().strip():
            return {"cmd": cmd_ed.text().strip(), "cd": cd_sp.value(), "min_qi": qi_sp.value()}
        return None

    def _on_menpai_change(self) -> None:
        key = self.menpai_cb.currentData()
        self.preset_cb.clear()
        self.preset_cb.addItem("— 选择该门派方案 —", None)
        mp = _MENPAI_PRESETS.get(key)
        if not mp:
            return
        for pr in mp.get("presets", []):
            self.preset_cb.addItem(pr.get("name") or "方案", pr)

    def _on_apply_preset(self) -> None:
        if self._engine is None:
            return
        pr = self.preset_cb.currentData()
        if not pr:
            return
        self._engine.set_rotation(list(pr.get("rotation") or []))
        self._reload_steps()
        # 应用方案时同步刷新战前准备提示（门派 hint）
        mp = _MENPAI_PRESETS.get(self.menpai_cb.currentData() or "")
        if mp and mp.get("jifa_hint"):
            self.status.setText(mp["jifa_hint"])
        self._log_now(f"已应用方案: {pr.get('name') or self.preset_cb.currentText()}")

    def _log_now(self, text: str) -> None:
        from PyQt6.QtCore import QDateTime
        ts = QDateTime.currentDateTime().toString("HH:mm:ss")
        self.log_list.addItem(f"[{ts}] {text}")
        self.log_list.scrollToBottom()

    def _on_add(self) -> None:
        if self._engine is None:
            return
        step = self._step_form(None)
        if step:
            rot = list(self._engine.rotation) + [step]
            self._engine.set_rotation(rot)
            self._reload_steps()

    def _on_edit(self) -> None:
        if self._engine is None:
            return
        row = self.step_list.currentRow()
        if row < 0:
            return
        step = self._step_form(self._engine.rotation[row])
        if step:
            rot = list(self._engine.rotation)
            rot[row] = step
            self._engine.set_rotation(rot)
            self._reload_steps()

    def _on_del(self) -> None:
        if self._engine is None:
            return
        row = self.step_list.currentRow()
        if row < 0:
            return
        rot = list(self._engine.rotation)
        rot.pop(row)
        self._engine.set_rotation(rot)
        self._reload_steps()

    def shutdown(self) -> None:
        self._unbind()
        self._engine = None