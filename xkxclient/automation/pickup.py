from __future__ import annotations

import time

from PyQt6.QtCore import QObject

from xkxclient.core.config import ConfigManager
from xkxclient.parse.look import _ENTITY_RE

# 拾取冷却：同一物品在冷却时间内不重复 get（房间内反复 look 也只会拾取一次；
# 失败（太重/未拾起）也会在冷却后于下一条房间信息重新尝试）
_PICK_COOLDOWN = 8.0


class AutoPickupEngine(QObject):
    """自动拾取引擎（常驻）：监控所有服务器房间信息。

    - 订阅所有上行文本（net.text_display）及 look 解析结果（look.parsed），
      识别 `中文名(英文名)`（如 `石炭(Shi tan)`）形式的物品；
    - 命中用户配置的物品（可填中文名或英文名）后自动发送 `get <英文名>`；
    - 英文名大小写不敏感；同一物品冷却期内不重复拾取。
    配置存 config.json `auto_pickup` = {"enabled": bool, "items": [名字…]}。
    """

    def __init__(self, bus, session, parent=None) -> None:
        super().__init__(parent)
        self.bus = bus
        self.session = session
        self.enabled = False
        self.items: list[str] = []
        self._cooldown: dict[str, float] = {}
        self._load_config()
        if bus is not None:
            bus.subscribe("net.text_display", self._on_line)
            bus.subscribe("look.parsed", self._on_look_parsed)

    # ---- 配置 ----
    def _load_config(self) -> None:
        cfg = ConfigManager.instance().get("auto_pickup") or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.items = [str(x) for x in (cfg.get("items") or [])]

    def set_config(self, enabled: bool, items: list[str]) -> None:
        self.enabled = enabled
        self.items = [str(x) for x in items if str(x).strip()]
        ConfigManager.instance().set("auto_pickup", {
            "enabled": enabled, "items": self.items})

    # ---- 匹配 ----
    def _matches(self, name_cn: str, name_en: str) -> bool:
        for it in self.items:
            it = it.strip()
            if not it:
                continue
            if it == name_cn:
                return True
            if name_en and it.lower() == name_en.lower():
                return True
        return False

    # ---- 监听 ----
    def _on_line(self, payload: dict) -> None:
        if not self.enabled:
            return
        if (payload.get("account") or "") != self.session.account_id:
            return
        line = payload.get("line") or ""
        for m in _ENTITY_RE.finditer(line):
            self._pick_entity(m.group(1), m.group(2))

    def _on_look_parsed(self, payload: dict) -> None:
        if not self.enabled:
            return
        if (payload.get("account") or "") != self.session.account_id:
            return
        result = payload.get("result")
        for ent in getattr(result, "entities", []):
            self._pick_entity(getattr(ent, "name", ""), getattr(ent, "english", ""))

    # ---- 拾取 ----
    def _pick_entity(self, name_cn: str, name_en: str) -> None:
        if not name_en or not self._matches(name_cn, name_en):
            return
        key = name_en.strip().lower()
        now = time.monotonic()
        if self._cooldown.get(key, 0.0) > now:
            return
        self._cooldown[key] = now + _PICK_COOLDOWN
        # 服务器物品英文 id 一律小写（如 get gold / get shi tan）；
        # look 显示的名字可能大写开头（Gold），直接发会被当作未知物品。
        self.session.send_auto(f"get {name_en.strip().lower()}")