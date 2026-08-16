"""客户端账号服务器处理（纯标准库，无框架依赖，供 map_server_v3.py 集成）。

功能：
- 注册 / 登录（token 认证）
- 客户端设置云同步（每个账号独立一份）
- 自动化设置云同步（每个账号独立一份）
- 宏分享署名（分享/删除按客户端账号）

存储：目录下 users/ 文件夹，每个账号一个子目录 <safe_uid>/，
    settings.json  ← 客户端设置
    automation.json ← 自动化设置
    password.hash  ← 密码哈希（sha256 + 随机盐）
账号口令只存哈希，不存明文。

端点约定（由调用方路由）：
    GET  /api/user/list        → {ok, count, users:[...]}   （仅列出已注册账号名，供注册查重）
    GET  /api/macros/delete?name&token → 删除自己分享的宏（宏分享归属在 macro_server）

POST（body JSON）：
    /api/user/register    → {username, password}          → {ok, token}
    /api/user/login       → {username, password}          → {ok, token}
    /api/user/settings/upload   → {token, settings}       → {ok}
    /api/user/settings/download → {token}                 → {ok, settings}
    /api/user/automation/upload → {token, automation}     → {ok}
    /api/user/automation/download → {token}               → {ok, automation}
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time

USERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users")
MAX_SETTINGS = 2 * 1024 * 1024      # 设置/自动化上传上限 2MB
MAX_UID = 40
TOKEN_TTL = 60 * 60 * 24 * 30       # token 有效期 30 天（服务端仅存哈希，登录校验用）


def _safe_uid(username: str) -> str:
    """账号名清洗：仅保留汉字/字母/数字/_/-，截断。"""
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_\-]", "", username or "")
    return s[:MAX_UID].strip()


def _user_dir(uid: str) -> str:
    return os.path.join(USERS_DIR, uid)


def _pass_hash(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(8)
    h = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return h, salt


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _save_auth(uid: str, pass_hash: str, salt: str) -> None:
    os.makedirs(_user_dir(uid), exist_ok=True)
    with open(os.path.join(_user_dir(uid), "password.hash"), "w", encoding="utf-8") as f:
        json.dump({"hash": pass_hash, "salt": salt}, f)


def _read_auth(uid: str) -> tuple[str, str] | None:
    try:
        with open(os.path.join(_user_dir(uid), "password.hash"), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("hash", ""), data.get("salt", "")
    except (OSError, ValueError, TypeError):
        return None


def _check_token(token: str) -> str | None:
    """校验 token 是否有效（与任一账号的 token 哈希比对）。返回账号 uid。"""
    if not token or not os.path.isdir(USERS_DIR):
        return None
    th = _token_hash(token)
    for uid in os.listdir(USERS_DIR):
        if not uid.startswith(".") and os.path.isdir(_user_dir(uid)):
            f = os.path.join(_user_dir(uid), "token.hash")
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("hash") == th:
                    return uid
            except (OSError, ValueError, TypeError):
                continue
    return None


def _write_token(uid: str, token: str) -> None:
    with open(os.path.join(_user_dir(uid), "token.hash"), "w", encoding="utf-8") as f:
        json.dump({"hash": _token_hash(token), "time": time.time()}, f)


def _read_file(uid: str, name: str):
    try:
        with open(os.path.join(_user_dir(uid), name), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return None


def _write_file(uid: str, name: str, data) -> bool:
    try:
        raw = json.dumps(data, ensure_ascii=False)
        if len(raw.encode("utf-8")) > MAX_SETTINGS:
            return False
        os.makedirs(_user_dir(uid), exist_ok=True)
        with open(os.path.join(_user_dir(uid), name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


# ── 对外 API ──────────────────────────────────────────

def register(username: str, password: str) -> tuple[int, dict]:
    uid = _safe_uid(username)
    if not uid:
        return 400, {"ok": False, "error": "账号名不合法"}
    if len(password) < 4:
        return 400, {"ok": False, "error": "密码至少 4 位"}
    if os.path.exists(_user_dir(uid)):
        return 400, {"ok": False, "error": "该客户端账号已注册"}
    ph, salt = _pass_hash(password)
    _save_auth(uid, ph, salt)
    token = secrets.token_hex(24)
    _write_token(uid, token)
    return 200, {"ok": True, "token": token, "username": uid}


def login(username: str, password: str) -> tuple[int, dict]:
    uid = _safe_uid(username)
    auth = _read_auth(uid) if uid else None
    if auth is None:
        return 400, {"ok": False, "error": "账号不存在或密码错误"}
    ph, salt = auth
    if _pass_hash(password, salt)[0] != ph:
        return 400, {"ok": False, "error": "账号不存在或密码错误"}
    token = secrets.token_hex(24)
    _write_token(uid, token)
    return 200, {"ok": True, "token": token, "username": uid}


def list_users() -> dict:
    if not os.path.isdir(USERS_DIR):
        return {"ok": True, "count": 0, "users": []}
    users = [u for u in os.listdir(USERS_DIR)
             if not u.startswith(".") and os.path.isdir(_user_dir(u))]
    return {"ok": True, "count": len(users), "users": sorted(users)}


def upload_settings(token: str, settings) -> tuple[int, dict]:
    uid = _check_token(token)
    if uid is None:
        return 401, {"ok": False, "error": "登录已失效，请重新登录"}
    if not _write_file(uid, "settings.json", settings):
        return 400, {"ok": False, "error": "设置数据过大或写入失败"}
    return 200, {"ok": True, "username": uid}


def download_settings(token: str) -> tuple[int, dict]:
    uid = _check_token(token)
    if uid is None:
        return 401, {"ok": False, "error": "登录已失效，请重新登录"}
    data = _read_file(uid, "settings.json")
    if data is None:
        return 404, {"ok": False, "error": "服务器尚无该账号的设置"}
    return 200, {"ok": True, "settings": data}


def upload_automation(token: str, automation) -> tuple[int, dict]:
    uid = _check_token(token)
    if uid is None:
        return 401, {"ok": False, "error": "登录已失效，请重新登录"}
    if not _write_file(uid, "automation.json", automation):
        return 400, {"ok": False, "error": "自动化数据过大或写入失败"}
    return 200, {"ok": True, "username": uid}


def download_automation(token: str) -> tuple[int, dict]:
    uid = _check_token(token)
    if uid is None:
        return 401, {"ok": False, "error": "登录已失效，请重新登录"}
    data = _read_file(uid, "automation.json")
    if data is None:
        return 404, {"ok": False, "error": "服务器尚无该账号的自动化设置"}
    return 200, {"ok": True, "automation": data}


# ── 路由 ──────────────────────────────────────────

def handle_get_user(query: dict, path: str) -> tuple[int, dict]:
    if path == "/api/user/list":
        return 200, list_users()
    return 404, {"ok": False, "error": "not found"}


def handle_post_user(path: str, body: dict) -> tuple[int, dict]:
    if path == "/api/user/register":
        return register(str(body.get("username") or ""), str(body.get("password") or ""))
    if path == "/api/user/login":
        return login(str(body.get("username") or ""), str(body.get("password") or ""))
    if path == "/api/user/settings/upload":
        return upload_settings(str(body.get("token") or ""), body.get("settings"))
    if path == "/api/user/settings/download":
        return download_settings(str(body.get("token") or ""))
    if path == "/api/user/automation/upload":
        return upload_automation(str(body.get("token") or ""), body.get("automation"))
    if path == "/api/user/automation/download":
        return download_automation(str(body.get("token") or ""))
    return 404, {"ok": False, "error": "not found"}