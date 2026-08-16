from __future__ import annotations

from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFontComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from xkxclient.core.config import ConfigManager
from xkxclient.core.shortcuts import ShortcutManager


# 服务器环境变量快捷设置（wiki pkuxkx:envariable）
# (显示名, set 命令, [可选值说明])
_ENV_PRESETS = [
    ("省略战斗信息 (skip_combat 1)", "set skip_combat 1", "普通招式"),
    ("省略战斗信息 (skip_combat 2)", "set skip_combat 2", "回合提示"),
    ("省略战斗信息 (skip_combat 3)", "set skip_combat 3", "仅受伤"),
    ("忽略wield信息 (nowieldmsg)", "set nowieldmsg 1", ""),
    ("自定义hp数值", "set custom_hp 1", ""),
    ("hp带千分位分隔", "set custom_hp 2", ""),
    ("广播战斗信息", "set broadcast_combat 1", ""),
    ("战斗额外报告", "set combat_report 1", ""),
    ("移动只看短名 (brief 1)", "set brief 1", ""),
    ("显示出口物品 (brief 2)", "set brief 2", ""),
    ("学习emote", "set learn_emote 1", ""),
    ("不显示wield信息", "set nowieldmsg 1", ""),
    ("取消即可", "unset skip_combat", ""),
]


class EnvSettingsDialog(QDialog):
    """服务器环境变量快捷设置（一键发送 set/unset）。"""

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("服务器环境变量")
        self.setMinimumWidth(360)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for label, cmd, hint in _ENV_PRESETS:
            self.list.addItem(f"{label}   ({cmd})")
        self.apply_btn = QPushButton("应用选中")
        self.apply_btn.clicked.connect(self._apply)
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("环境变量（对当前账号生效，随人物保存）"))
        lay.addWidget(self.list, 1)
        btns = QHBoxLayout()
        btns.addWidget(self.apply_btn)
        btns.addWidget(self.close_btn)
        lay.addLayout(btns)

    def _apply(self) -> None:
        item = self.list.currentItem()
        if not item or self.session is None:
            return
        text = item.text()
        cmd = text[text.index("(") + 1:text.rindex(")")].strip()
        for _, preset, _h in _ENV_PRESETS:
            if cmd in preset:
                if self.session.logged_in:
                    self.session.send(cmd)
                else:
                    self.session.connection.send_line(cmd)
                break


class ShortcutDialog(QDialog):
    """E8 快捷键设置：动作 | 当前按键 | 重绑 | 重置。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("快捷键设置")
        self.sm = ShortcutManager.instance()
        self.list = QListWidget()
        self._rebuild()
        self.rebind_btn = QPushButton("重绑选中…")
        self.reset_btn = QPushButton("全部重置")
        self.rebind_btn.clicked.connect(self._rebind)
        self.reset_btn.clicked.connect(self._reset)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("快捷键"))
        lay.addWidget(self.list, 1)
        btns = QHBoxLayout()
        btns.addWidget(self.rebind_btn)
        btns.addWidget(self.reset_btn)
        lay.addLayout(btns)

    def _rebuild(self) -> None:
        self.list.clear()
        for name, seq in self.sm.all_bindings().items():
            self.list.addItem(f"{name}: {seq}")

    def _rebind(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        name = item.text().split(":")[0]
        seq, ok = QInputDialog.getText(self, "重绑", f"{name} 新按键（如 Ctrl+X）",
                                       text=item.text().split(": ")[-1])
        if ok and seq.strip():
            self.sm.rebind(name, seq.strip())
            item.setText(f"{name}: {seq.strip()}")

    def _reset(self) -> None:
        self.sm.reset_all()
        self._rebuild()


# 关闭行为配置键的取值：always_tray / always_quit / ask（默认）
_CLOSE_MODE_KEY = "close.mode"
# 退出时是否向服务器发送 quit（存档/清理）。默认 True。
_SEND_QUIT_KEY = "close.send_quit"


class CloseBehaviorDialog(QDialog):
    """关闭行为设置：关闭按钮是退出还是缩到托盘，或每次询问；修改这里可改变保存过的选择。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("关闭行为")
        self.setMinimumWidth(360)
        self.ask_rb = QRadioButton("每次询问（推荐）")
        self.tray_rb = QRadioButton("关闭即缩到系统托盘")
        self.quit_rb = QRadioButton("关闭即直接退出")
        mode = ConfigManager.instance().get(_CLOSE_MODE_KEY, "ask")
        if mode == "always_tray":
            self.tray_rb.setChecked(True)
        elif mode == "always_quit":
            self.quit_rb.setChecked(True)
        else:
            self.ask_rb.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.ask_rb)
        group.addButton(self.tray_rb)
        group.addButton(self.quit_rb)

        self.send_quit_cb = QCheckBox("退出前发送 quit（让服务器存档/清理）")
        self.send_quit_cb.setChecked(
            bool(ConfigManager.instance().get(_SEND_QUIT_KEY, True)))

        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton("取消")
        close_btn.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("点击主窗口关闭按钮时的行为："))
        lay.addWidget(self.ask_rb)
        lay.addWidget(self.tray_rb)
        lay.addWidget(self.quit_rb)
        lay.addWidget(self.send_quit_cb)
        btns = QHBoxLayout()
        btns.addWidget(save_btn)
        btns.addWidget(close_btn)
        lay.addLayout(btns)

    def _save(self) -> None:
        if self.tray_rb.isChecked():
            mode = "always_tray"
        elif self.quit_rb.isChecked():
            mode = "always_quit"
        else:
            mode = "ask"
        ConfigManager.instance().set(_CLOSE_MODE_KEY, mode)
        ConfigManager.instance().set(_SEND_QUIT_KEY, self.send_quit_cb.isChecked())
        self.accept()


class SettingsDialog(QDialog):
    """统一设置窗口（服务器→设置）：标签页合并关闭行为/字体/主题/布局/编码/快捷键。"""

    def __init__(self, main_window, parent=None) -> None:
        super().__init__(parent)
        self.main = main_window
        self.setWindowTitle("设置")
        self.setMinimumSize(420, 380)
        tabs = QTabWidget(self)
        tabs.addTab(self._tab_general(), "通用")
        tabs.addTab(self._tab_font(), "字体")
        tabs.addTab(self._tab_layout(), "布局")
        tabs.addTab(self._tab_encoding(), "编码")
        tabs.addTab(self._tab_shortcuts(), "快捷键")
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        box.rejected.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs, 1)
        lay.addWidget(box)

    # ---- 通用：关闭行为 ----
    def _tab_general(self) -> QWidget:
        w = QWidget()
        self.ask_rb = QRadioButton("每次询问（推荐）")
        self.tray_rb = QRadioButton("关闭即缩到系统托盘")
        self.quit_rb = QRadioButton("关闭即直接退出")
        mode = ConfigManager.instance().get(_CLOSE_MODE_KEY, "ask")
        if mode == "always_tray":
            self.tray_rb.setChecked(True)
        elif mode == "always_quit":
            self.quit_rb.setChecked(True)
        else:
            self.ask_rb.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.ask_rb)
        group.addButton(self.tray_rb)
        group.addButton(self.quit_rb)
        self.send_quit_cb = QCheckBox("退出前发送 quit（让服务器存档/清理）")
        self.send_quit_cb.setChecked(bool(ConfigManager.instance().get(_SEND_QUIT_KEY, True)))
        save = QPushButton("保存")
        save.clicked.connect(self._save_close)
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("点击主窗口关闭按钮时的行为："))
        lay.addWidget(self.ask_rb)
        lay.addWidget(self.tray_rb)
        lay.addWidget(self.quit_rb)
        lay.addWidget(self.send_quit_cb)
        lay.addWidget(save)
        lay.addStretch(1)
        return w

    def _save_close(self) -> None:
        if self.tray_rb.isChecked():
            mode = "always_tray"
        elif self.quit_rb.isChecked():
            mode = "always_quit"
        else:
            mode = "ask"
        ConfigManager.instance().set(_CLOSE_MODE_KEY, mode)
        ConfigManager.instance().set(_SEND_QUIT_KEY, self.send_quit_cb.isChecked())

    # ---- 字体 ----
    def _tab_font(self) -> QWidget:
        w = QWidget()
        cur = ConfigManager.instance().get("font", {"family": "Consolas", "size": 12})
        self.font_combo = QFontComboBox(w)
        self.font_combo.addItems(["Consolas", "NSimSun", "新宋体", "Courier New", "SimHei",
                                  "DejaVu Sans Mono", "微软雅黑"])
        self.font_combo.setCurrentText(str(cur.get("family", "Consolas")))
        self.font_spin = QSpinBox(w)
        self.font_spin.setRange(6, 48)
        self.font_spin.setValue(int(cur.get("size", 12)))
        apply = QPushButton("应用")
        apply.clicked.connect(self._apply_font)
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("等宽字体："))
        lay.addWidget(self.font_combo)
        lay.addWidget(QLabel("字号："))
        lay.addWidget(self.font_spin)
        lay.addWidget(apply)
        lay.addStretch(1)
        return w

    def _apply_font(self) -> None:
        family = self.font_combo.currentText()
        size = self.font_spin.value()
        spec = {"family": family, "size": size}
        ConfigManager.instance().set("font", spec)
        tab = self.main._tab()
        if tab is not None and hasattr(tab.output, "set_font_spec"):
            tab.output.set_font_spec(spec)

    # ---- 布局 ----
    def _tab_layout(self) -> QWidget:
        w = QWidget()
        btn = QPushButton("重置窗口布局")
        btn.clicked.connect(self.main._reset_layout)
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("将恢复客户端默认的面板排列。"))
        lay.addWidget(btn)
        lay.addStretch(1)
        return w

    # ---- 编码 ----
    def _tab_encoding(self) -> QWidget:
        w = QWidget()
        tab = self.main._tab()
        cur = getattr(getattr(tab, "session", None), "encoding", "gbk")
        self.enc_cb = QComboBox(w)
        self.enc_cb.addItems(["gbk", "utf-8"])
        idx = self.enc_cb.findText(str(cur).lower())
        self.enc_cb.setCurrentIndex(max(0, idx))
        apply = QPushButton("应用")
        apply.clicked.connect(self._apply_encoding)
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("连接编码（GBK / UTF-8）："))
        lay.addWidget(self.enc_cb)
        lay.addWidget(apply)
        lay.addStretch(1)
        return w

    def _apply_encoding(self) -> None:
        tab = self.main._tab()
        if tab is not None and hasattr(tab.session, "set_encoding"):
            tab.session.set_encoding(self.enc_cb.currentText())

    # ---- 快捷键 ----
    def _tab_shortcuts(self) -> QWidget:
        w = QWidget()
        self.sc_list = QListWidget(w)
        self.sc_sm = ShortcutManager.instance()
        self._sc_rebuild()
        rebind = QPushButton("重绑选中…")
        reset = QPushButton("全部重置")
        rebind.clicked.connect(self._sc_rebind)
        reset.clicked.connect(self._sc_reset)
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("快捷键："))
        lay.addWidget(self.sc_list, 1)
        btns = QHBoxLayout()
        btns.addWidget(rebind)
        btns.addWidget(reset)
        lay.addLayout(btns)
        return w

    def _sc_rebuild(self) -> None:
        self.sc_list.clear()
        for name, seq in self.sc_sm.all_bindings().items():
            self.sc_list.addItem(f"{name}: {seq}")

    def _sc_rebind(self) -> None:
        item = self.sc_list.currentItem()
        if not item:
            return
        name = item.text().split(":")[0]
        seq, ok = QInputDialog.getText(self, "重绑", f"{name} 新按键（如 Ctrl+X）",
                                       text=item.text().split(": ")[-1])
        if ok and seq.strip():
            self.sc_sm.rebind(name, seq.strip())
            self._sc_rebuild()

    def _sc_reset(self) -> None:
        self.sc_sm.reset_all()
        self._sc_rebuild()