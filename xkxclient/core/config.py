from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject

from xkxclient.core.bus import EventBus

APP_DATA_ROOT = Path(os.environ.get("APPDATA", str(Path.home()))) / "XkxClient"
DEFAULT_SERVERS = [
    {"name": "北侠", "host": "mud.pkuxkx.net", "port": 8080, "encoding": "gbk"},
    {"name": "北侠(UTF8)", "host": "mud.pkuxkx.net", "port": 8081, "encoding": "utf-8"},
    {"name": "备用/本机", "host": "127.0.0.1", "port": 4000, "encoding": "gbk"},
]

DEFAULTS = {
    "window": {"width": 1280, "height": 820},
    "font": {"family": "SimHei", "size": 12},
    "last_server": "mud.pkuxkx.net",
    "last_port": 8080,
}


def json_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def json_read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


class ConfigManager(QObject):
    """配置管理单例（wiki E8-配置管理.md）。根目录 %APPDATA%\\XkxClient\\。"""

    _instance: "ConfigManager | None" = None

    def __init__(self, bus: EventBus | None = None, root: Path | None = None) -> None:
        super().__init__()
        self.bus = bus
        self.root = root or APP_DATA_ROOT
        self._data = json_read(self.root / "config.json")
        if self._data.get("servers") is None:
            self._data["servers"] = DEFAULT_SERVERS
            self.save()
        self._accounts: dict[str, dict] = {}
        self._automation: dict[str, dict] = {}

    @classmethod
    def instance(cls) -> "ConfigManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---- 全局 ----
    def save(self) -> None:
        json_write(self.root / "config.json", self._data)

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, path: str, value: Any) -> None:
        keys = path.split(".")
        node: dict = self._data
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value
        self.save()
        if self.bus is not None:
            self.bus.publish("config.changed", scope="global", path=path, value=value)

    # ---- 服务器 ----
    @property
    def servers(self) -> list[dict]:
        return self._data.get("servers", [])

    def save_server(self, server: dict) -> None:
        servers = [s for s in self.servers if s["name"] != server["name"]]
        servers.append(server)
        self.set("servers", servers)

    # ---- 账号 ----
    def accounts(self) -> dict[str, dict]:
        """返回 {account_id: {username, password}}。兼容旧版 list 格式自动迁移。"""
        raw = json_read(self.root / "accounts.json")
        if isinstance(raw, list):  # 旧格式迁移
            out: dict[str, dict] = {}
            for item in raw:
                if isinstance(item, dict) and item.get("username"):
                    out[item["username"]] = item
            return out
        if not isinstance(raw, dict):
            return {}
        return {k: v for k, v in raw.items() if isinstance(v, dict)}

    def save_account(self, account_id: str, data: dict) -> None:
        accs = self.accounts()
        accs[account_id] = data
        json_write(self.root / "accounts.json", accs)

    def remove_account(self, account_id: str) -> None:
        accs = self.accounts()
        accs.pop(account_id, None)
        json_write(self.root / "accounts.json", accs)

    def account_file(self, account_id: str) -> Path:
        return self.root / "accounts" / account_id

    # ---- 自动化定义（共享存全局，账号存账号目录）----
    def automation(self, account_id: str | None) -> dict:
        """加载账号自动化定义（含共享）。返回 {triggers:[], aliases:[], timers:[], macros:[]}

        共享项标注 `shared: True`，账号项为 `shared: False`（供编辑器正确回写作用域）。
        """
        shared = json_read(self.root / "automation_shared.json")
        if account_id:
            own = json_read(self.account_file(account_id) / "automation.json")
        else:
            own = {}
        merged = {}
        for key in ("triggers", "aliases", "timers", "macros"):
            items = []
            for d in list(shared.get(key, [])):
                it = dict(d)
                it["shared"] = True
                items.append(it)
            for d in list(own.get(key, [])):
                it = dict(d)
                it["shared"] = False
                items.append(it)
            merged[key] = items
        return merged

    def save_automation(self, account_id: str | None, kind: str, items: list[dict]) -> None:
        if account_id is None:
            path = self.root / "automation_shared.json"
        else:
            path = self.account_file(account_id) / "automation.json"
        data = json_read(path)
        data[kind] = items
        json_write(path, data)

    def save_all(self) -> None:
        self.save()