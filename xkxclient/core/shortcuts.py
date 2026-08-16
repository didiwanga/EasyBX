from __future__ import annotations

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QAction, QKeySequence

from xkxclient.core.config import ConfigManager

# E8-快捷键 预设
PRESETS: dict[str, str] = {
    "new_tab": "Ctrl+N",
    "close_tab": "Ctrl+W",
    "next_tab": "Ctrl+Tab",
    "prev_tab": "Ctrl+Shift+Tab",
    "disconnect": "Ctrl+Shift+D",
    "reconnect": "Ctrl+Shift+R",
    "quit": "Ctrl+Q",
    "find": "Ctrl+F",
    "find_next": "F3",
    "clean": "Ctrl+Shift+C",
    "clear_output": "Ctrl+L",
    "font": "Ctrl+Shift+F",
    "split": "Ctrl+Shift+V",
    "trigger_edit": "Ctrl+1",
    "alias_edit": "Ctrl+2",
    "timer_edit": "Ctrl+3",
    "macro_edit": "Ctrl+4",
    "script_edit": "Ctrl+5",
    "commands_panel": "Ctrl+6",
    "local_map": "Ctrl+M",
    "world_map": "Ctrl+Shift+M",
    "fullscreen": "F11",
}


class ShortcutManager(QObject):
    """E8-快捷键单例：注册/重绑/应用（config keybinds）/冲突校验。"""

    _instance: "ShortcutManager | None" = None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._actions: dict[str, QAction] = {}
        self._cb: dict[str, callable] = {}
        self._overrides: dict[str, str] = {}
        cm = ConfigManager.instance()
        raw = cm.get("keybinds")
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(v, str):
                    self._overrides[k] = v

    @classmethod
    def instance(cls, parent=None) -> "ShortcutManager":
        if cls._instance is None:
            cls._instance = cls(parent)
        return cls._instance

    def register(self, name: str, callback, seq: str | None = None) -> QAction | None:
        """在 parent 上创建 QAction；seq 缺省用 override 或 PRESETS。"""
        seq = seq or self._overrides.get(name) or PRESETS.get(name)
        if seq is None or self._callback_owner is None:
            return None
        action = QAction(seq, self._callback_owner)
        action.setShortcut(QKeySequence(seq))
        action.triggered.connect(callback)
        self._actions[name] = action
        self._cb[name] = callback
        return action

    @property
    def _callback_owner(self):
        # 由 MainWindow 注入宿主做 QAction 父对象
        return getattr(self, "_host", None)

    def attach(self, host) -> None:
        self._host = host

    def binding(self, name: str) -> str:
        return self._overrides.get(name, PRESETS.get(name, ""))

    def rebind(self, name: str, seq: str) -> None:
        self._overrides[name] = seq
        act = self._actions.get(name)
        if act is not None:
            act.setShortcut(QKeySequence(seq))
        cm = ConfigManager.instance()
        data = dict(cm.get("keybinds") or {})
        data[name] = seq
        cm.set("keybinds", data)

    def reset_all(self) -> None:
        for name, seq in PRESETS.items():
            if name in self._overrides:
                self.rebind(name, seq)

    def all_bindings(self) -> dict[str, str]:
        return {k: self._overrides.get(k, v) for k, v in PRESETS.items()}