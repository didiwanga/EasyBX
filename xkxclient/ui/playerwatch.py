from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from xkxclient.automation.playerwatch import parse_player
from xkxclient.core.config import ConfigManager


class PlayerWatchDialog(QDialog):
    """发现玩家设置：开关 + 玩家列表（中文名(英文名) + 触发指令）。

    规则存 config.json `player_watch` = {"enabled": bool, "players":
    [{"cn": 中文名, "en": 英文名, "cmd": 指令}]}。
    指令中 `<cn>` 替换为中文名，`<en>` 替换为英文名（发送时全小写）。
    """

    def __init__(self, session=None, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("发现玩家")
        self.setMinimumWidth(460)
        self.setMinimumHeight(360)
        self.config = ConfigManager.instance()
        cfg = self.config.get("player_watch") or {}
        self.players: list[dict] = []
        for p in (cfg.get("players") or []):
            if isinstance(p, dict) and p.get("cn"):
                self.players.append(dict(p))

        self.enabled_cb = QCheckBox("启用发现玩家（常驻，监控所有服务器信息）")
        self.enabled_cb.setChecked(bool(cfg.get("enabled", False)))

        self.beep_cb = QCheckBox("命中提示音")
        self.beep_cb.setToolTip("命中玩家时播放一声「叮」提醒")
        self.beep_cb.setChecked(bool(cfg.get("beep", True)))

        hint = QLabel("添加「中文名(英文名)」。服务器信息包含中文名或英文名"
                      "（英文不区分大小写）时发送你设定的指令。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#808080;")

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_select)

        self.input = QLineEdit()
        self.input.setPlaceholderText("中文名(英文名)")
        self.input.returnPressed.connect(self._add)

        self.cmd_ed = QLineEdit()
        self.cmd_ed.setPlaceholderText("触发指令，如 wenhao <cn> / kill <en>（支持 ; 分隔多条，<en> 发送时全小写）")
        self.cmd_ed.returnPressed.connect(self._add)

        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self._add)
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self._delete)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.input, 3)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)

        close_btn = QPushButton("保存并关闭")
        close_btn.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(self.enabled_cb)
        lay.addWidget(self.beep_cb)
        lay.addWidget(hint)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.cmd_ed)
        lay.addLayout(btn_row)
        lay.addWidget(close_btn)
        self._refresh()
        if self.players:
            self.list.setCurrentRow(0)

    def _refresh(self) -> None:
        self.list.clear()
        for p in self.players:
            label = f"{p.get('cn', '')}({p.get('en', '')})"
            cmd = p.get("cmd", "")
            if cmd:
                label += f"  →  {cmd}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, p)
            self.list.addItem(item)

    def _on_select(self, row: int) -> None:
        if 0 <= row < len(self.players):
            self.cmd_ed.setText(self.players[row].get("cmd", ""))

    def _add(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        parsed = parse_player(text)
        if parsed is None:
            self.input.selectAll()
            return
        cn, en = parsed
        cmd = self.cmd_ed.text().strip()
        for p in self.players:
            if p.get("cn") == cn:
                # 同名更新（含英文名与指令）
                p["en"] = en
                p["cmd"] = cmd
                self._refresh()
                self.list.setCurrentRow(self.players.index(p))
                self.input.clear()
                self.input.setFocus()
                return
        self.players.append({"cn": cn, "en": en, "cmd": cmd})
        self._refresh()
        self.list.setCurrentRow(len(self.players) - 1)
        self.input.clear()
        self.input.setFocus()

    def _delete(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.players):
            self.players.pop(row)
            self.cmd_ed.clear()
            self._refresh()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete and self.list.hasFocus():
            self._delete()
            return
        super().keyPressEvent(event)

    def accept(self) -> None:
        players = [p for p in self.players if str(p.get("cn", "")).strip()]
        self.config.set("player_watch", {
            "enabled": self.enabled_cb.isChecked(),
            "beep": self.beep_cb.isChecked(), "players": players})
        if self.session is not None:
            eng = getattr(self.session, "player_watch", None)
            if eng is not None:
                eng.set_config(self.enabled_cb.isChecked(), players,
                               self.beep_cb.isChecked())
        super().accept()
