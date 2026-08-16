from __future__ import annotations

import re
import time

from PyQt6.QtCore import QObject

from xkxclient.core.config import ConfigManager

# 同一玩家触发冷却：防止房间内常驻玩家被反复出现的名字无限触发
# （同屏列表/重复房间描述会连续命中同一名字）。
_REPEAT_COOLDOWN = 10.0

# 玩家条目：中文名(英文名)，如 乐师(Ccbv)
_PLAYER_RE = re.compile(r"([\u4e00-\u9fff·]{1,6})\s*\(([A-Za-z][A-Za-z ]*)\)")


def parse_player(text: str) -> tuple[str, str] | None:
    """从 `中文名(英文名)` 解析出 (中文名, 英文名)；非法返回 None。"""
    m = _PLAYER_RE.search(text.strip())
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


class PlayerWatchEngine(QObject):
    """发现玩家引擎（常驻）：监控所有服务器文本。

    - 玩家以 `中文名(英文名)` 配置，每条可带触发指令；
    - 文本行包含中文名或英文名（英文大小写不敏感）即触发，发送用户指令；
    - 指令中的 `<cn>` 替换为中文名，`<en>` 替换为英文名（全小写）；
    - 同一玩家冷却期内不重复触发。
    配置存 config.json `player_watch` = {"enabled": bool, "players": [...]}。
    """

    def __init__(self, bus, session, parent=None) -> None:
        super().__init__(parent)
        self.bus = bus
        self.session = session
        self.enabled = False
        self.players: list[dict] = []
        self._cooldown: dict[str, float] = {}
        self._load_config()
        if bus is not None:
            bus.subscribe("net.text_display", self._on_line)

    # ---- 配置 ----
    def _load_config(self) -> None:
        cfg = ConfigManager.instance().get("player_watch") or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.players = []
        for p in (cfg.get("players") or []):
            if isinstance(p, dict) and p.get("cn"):
                self.players.append({
                    "cn": str(p.get("cn", "")),
                    "en": str(p.get("en", "")),
                    "cmd": str(p.get("cmd", "")),
                })

    def set_config(self, enabled: bool, players: list[dict]) -> None:
        self.enabled = bool(enabled)
        self.players = []
        for p in players:
            cn = str(p.get("cn", "")).strip()
            if cn:
                self.players.append({
                    "cn": cn,
                    "en": str(p.get("en", "")).strip(),
                    "cmd": str(p.get("cmd", "")).strip(),
                })
        ConfigManager.instance().set("player_watch", {
            "enabled": self.enabled, "players": self.players})

    # ---- 触发 ----
    def _on_line(self, payload: dict) -> None:
        if not self.enabled:
            return
        if (payload.get("account") or "") != self.session.account_id:
            return
        line = payload.get("line") or ""
        low = line.lower()
        now = time.monotonic()
        for p in self.players:
            cmd = p.get("cmd", "").strip()
            cn = p.get("cn", "")
            en = p.get("en", "")
            if not cmd:
                continue
            # 命中条件：行含中文名 或 行含英文名（英文大小写不敏感）
            hit = bool(cn and cn in line) or bool(en and en.lower() in low)
            if not hit:
                continue
            key = en.lower() if en else cn
            if self._cooldown.get(key, 0.0) > now:
                continue
            self._cooldown[key] = now + _REPEAT_COOLDOWN
            out = cmd.replace("<cn>", cn).replace("<en>", en.lower())
            self.session.send_auto(out)
