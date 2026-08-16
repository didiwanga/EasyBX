"""客户端账号：注册/登录/云同步（设置与自动化）网络封装。

服务端接口（server/user_server.py）：
    POST /api/user/register    {username, password}      → {ok, token, username}
    POST /api/user/login       {username, password}      → {ok, token, username}
    POST /api/user/settings/upload     {token, settings}
    POST /api/user/settings/download   {token}           → {ok, settings}
    POST /api/user/automation/upload   {token, automation}
    POST /api/user/automation/download {token}           → {ok, automation}

「客户端设置」= config.json（全局，不含账号密码——账号密码存于 accounts.json，
本模块绝不读取/上传账号密码）。「自动化设置」= automation_shared.json +
各账号 automation.json 打包。
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error

from xkxclient.version import (
    USER_REGISTER_URL, USER_LOGIN_URL,
    USER_SETTINGS_UPLOAD_URL, USER_SETTINGS_DOWNLOAD_URL,
    USER_AUTOMATION_UPLOAD_URL, USER_AUTOMATION_DOWNLOAD_URL,
)

_TIMEOUT = 20


class ClientUserError(RuntimeError):
    """客户端账号网络/服务器错误。"""


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(url: str, body: dict) -> dict:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with _opener().open(req, timeout=_TIMEOUT) as r:
            raw = r.read()
            text = raw.decode("utf-8-sig", errors="replace")
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8-sig", errors="replace")
        msg = ""
        try:
            parsed = json.loads(body_text)
            if isinstance(parsed, dict) and parsed.get("error"):
                msg = parsed["error"]
        except (ValueError, TypeError):
            pass
        raise ClientUserError(msg or f"服务器返回 HTTP {exc.code}") from exc
    except OSError as exc:
        raise ClientUserError(f"网络错误：{exc}") from exc
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ClientUserError("服务器响应不是有效 JSON") from exc
    if not isinstance(data, dict) or not data.get("ok"):
        raise ClientUserError(str(data.get("error") or "服务器返回异常"))
    return data


def register(username: str, password: str) -> str:
    """注册客户端账号，成功返回 token。"""
    data = _post(USER_REGISTER_URL, {"username": username, "password": password})
    return str(data.get("token") or "")


def login(username: str, password: str) -> str:
    """登录客户端账号，成功返回 token。"""
    data = _post(USER_LOGIN_URL, {"username": username, "password": password})
    return str(data.get("token") or "")


def upload_settings(token: str, settings: dict) -> None:
    _post(USER_SETTINGS_UPLOAD_URL, {"token": token, "settings": settings})


def download_settings(token: str) -> dict:
    data = _post(USER_SETTINGS_DOWNLOAD_URL, {"token": token})
    s = data.get("settings")
    if not isinstance(s, dict):
        raise ClientUserError("服务器设置数据异常")
    return s


def upload_automation(token: str, automation: dict) -> None:
    _post(USER_AUTOMATION_UPLOAD_URL, {"token": token, "automation": automation})


def download_automation(token: str) -> dict:
    data = _post(USER_AUTOMATION_DOWNLOAD_URL, {"token": token})
    a = data.get("automation")
    if not isinstance(a, dict):
        raise ClientUserError("服务器自动化数据异常")
    return a


# ---- 本机数据打包/解包（不含账号密码）----

def pack_settings(config) -> dict:
    """客户端设置打包：config.json 全量（global 作用域，无账号密码）。"""
    return dict(config._data or {})


def pack_automation(config) -> dict:
    """自动化设置打包：automation_shared.json + 各账号 automation.json（不含账号密码）。"""
    out: dict[str, object] = {"shared": json_read_file(config.root / "automation_shared.json"),
                              "accounts": {}}
    accs_dir = config.root / "accounts"
    if accs_dir.is_dir():
        for child in sorted(accs_dir.iterdir()):
            if child.is_dir():
                auto = child / "automation.json"
                if auto.exists():
                    out["accounts"][child.name] = json_read_file(auto)
    return out


def unpack_settings(config, settings: dict) -> None:
    """客户端设置解包：整体覆盖 config.json 数据（运行内存 + 落盘）。"""
    if not isinstance(settings, dict):
        raise ClientUserError("设置数据异常")
    config._data = dict(settings)
    if config._data.get("servers") is None:
        config._data["servers"] = list(config._data.get("servers") or [])
    config.save()


def unpack_automation(config, automation: dict) -> None:
    """自动化设置解包：覆盖 automation_shared.json + 各账号 automation.json。"""
    if not isinstance(automation, dict):
        raise ClientUserError("自动化数据异常")
    shared = automation.get("shared")
    if isinstance(shared, dict):
        write_json_file(config.root / "automation_shared.json", shared)
    accs = automation.get("accounts")
    if isinstance(accs, dict):
        for aid, auto in accs.items():
            if isinstance(auto, dict) and isinstance(aid, str) and aid:
                write_json_file(config.account_file(aid) / "automation.json", auto)


def json_read_file(path) -> dict:
    import json as _json
    try:
        return _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_json_file(path, data: dict) -> None:
    import json as _json
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)