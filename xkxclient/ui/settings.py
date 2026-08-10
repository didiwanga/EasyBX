from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

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