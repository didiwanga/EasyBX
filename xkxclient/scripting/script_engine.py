from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication

from xkxclient.core.config import ConfigManager
from xkxclient.scripting.lua_runtime import ScriptRunner

DEFAULT_TIMEOUT = 3600.0


class ScriptManager(QObject):
    """E8 脚本引擎：脚本库 CRUD + 持久化 + 运行注册表 + 自动加载。

    存储：%APPDATA%/XkxClient/scripts.json（name → {code, timeout, enabled}）。
    lua/ 目录仅用于 .lua 文件导入导出；运行一律从脚本库取代码。
    启用脚本在每次 `login.done` 时自动运行（按账号隔离运行实例）。
    """

    def __init__(self, app, parent=None) -> None:
        super().__init__(parent)
        self._app = app
        self._path = ConfigManager.instance().root / "scripts.json"
        self._data: dict = {"enabled": [], "scripts": {}}
        self._runners: dict[tuple[str, str], ScriptRunner] = {}
        self._load()
        self.auto_run = bool(ConfigManager.instance().get("scripts.auto_run", True))

    # ---- 持久化 ----
    def _load(self) -> None:
        try:
            if self._path.exists():
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {"enabled": [], "scripts": {}}
        self._data.setdefault("enabled", [])
        self._data.setdefault("scripts", {})

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except OSError:
            traceback.print_exc()

    # ---- 脚本库 CRUD ----
    def list(self) -> list[str]:
        return list(self._data["scripts"].keys())

    def enabled_names(self) -> list[str]:
        en = list(self._data.get("enabled") or [])
        return [n for n in en if n in self._data["scripts"]]

    def get(self, name: str) -> dict | None:
        d = self._data["scripts"].get(name)
        return dict(d) if d else None

    def code_of(self, name: str) -> str:
        d = self._data["scripts"].get(name) or {}
        return str(d.get("code") or "")

    def save(self, name: str, code: str, timeout: float = DEFAULT_TIMEOUT,
             enabled: bool | None = None) -> None:
        name = (name or "untitled").strip()
        old = self._data["scripts"].get(name) or {}
        if enabled is None:
            enabled = bool(old.get("enabled", False))
        self._data["scripts"][name] = {
            "code": code or "",
            "timeout": float(timeout or DEFAULT_TIMEOUT),
            "enabled": bool(enabled),
            "updated_at": int(time.time()),
        }
        if enabled and name not in self._data["enabled"]:
            self._data["enabled"].append(name)
        if not enabled:
            self._data["enabled"] = [n for n in self._data["enabled"] if n != name]
        self._save()

    def remove(self, name: str) -> None:
        self._data["scripts"].pop(name, None)
        self._data["enabled"] = [n for n in self._data["enabled"] if n != name]
        self._save()

    def set_enabled(self, name: str, enabled: bool) -> None:
        d = self._data["scripts"].get(name)
        if not d:
            return
        d["enabled"] = bool(enabled)
        if enabled:
            if name not in self._data["enabled"]:
                self._data["enabled"].append(name)
        else:
            self._data["enabled"] = [n for n in self._data["enabled"] if n != name]
        self._save()

    # ---- 导入 / 导出（lua/ 目录）----
    def import_lua(self, path: str) -> str:
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(path)
        code = src.read_text(encoding="utf-8", errors="replace")
        # lua/ 放用户数据目录（冻结打包后应用目录只读不可写）
        lua_dir = ConfigManager.instance().root / "lua"
        lua_dir.mkdir(parents=True, exist_ok=True)
        dst = lua_dir / src.name
        dst.write_text(code, encoding="utf-8")
        name = src.stem
        self.save(name, code)
        return name

    def export_lua(self, name: str, path: str) -> None:
        dst = Path(path)
        dst.write_text(self.code_of(name), encoding="utf-8")

    # ---- 运行 ----
    def run(self, session, name: str) -> ScriptRunner | None:
        d = self._data["scripts"].get(name)
        if not d:
            return None
        key = (session.account_id, name)
        old = self._runners.get(key)
        if old is not None and old.running:
            return old
        runner = ScriptRunner(session, self)
        timeout = self._script_timeout(d.get("code") or "", d.get("timeout"))
        runner.finished.connect(lambda ok, det: self._on_finished(key, ok, det))
        self._runners[key] = runner
        if not runner.start(d.get("code") or "", timeout):
            self._runners.pop(key, None)
            return None
        return runner

    @staticmethod
    def _script_timeout(code: str, stored) -> float:
        """脚本自带超时：头部 `-- timeout: <毫秒>` 注释优先；否则用已存值/默认。

        练功/挂机类脚本常驻运行，默认 60 秒会被强制中止，故支持脚本内声明。
        """
        import re
        m = re.search(r"--\s*timeout\s*:\s*(\d+)", code[:500])
        if m:
            return max(1.0, int(m.group(1)) / 1000.0)
        return float(stored or DEFAULT_TIMEOUT)

    def runner(self, account_id: str, name: str) -> ScriptRunner | None:
        key = (account_id, name)
        r = self._runners.get(key)
        if r is not None and r.running:
            return r
        return None

    def stop(self, account_id: str, name: str) -> None:
        r = self.runner(account_id, name)
        if r is not None:
            r.stop()

    def pause(self, account_id: str, name: str) -> None:
        r = self.runner(account_id, name)
        if r is not None:
            r.pause()

    def resume(self, account_id: str, name: str) -> None:
        r = self.runner(account_id, name)
        if r is not None:
            r.resume()

    def running_scripts(self) -> list[tuple[str, str]]:
        return [(a, n) for (a, n), r in self._runners.items() if r.running]

    def run_enabled(self, account_id: str) -> None:
        if not self.auto_run:
            return
        session = self._app.session(account_id)
        if session is None:
            return
        for name in self.enabled_names():
            try:
                # 延迟一拍启动，避免登录处理期间抢占主线程
                QApplication.processEvents()
                self.run(session, name)
            except Exception:
                traceback.print_exc()

    # ---- 内部 ----
    def _on_finished(self, key: tuple[str, str], ok: bool, detail: str) -> None:
        # 运行表保留句柄由 UI 连接信号；完成后移除
        self._runners.pop(key, None)

    def shutdown(self) -> None:
        for _key, r in self._runners.items():
            try:
                r.stop()
                r.dispose()
            except Exception:
                pass
        self._runners.clear()