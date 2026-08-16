"""宏分享服务器处理（纯标准库，无框架依赖，供 map_server_v3.py 集成）。

存储：目录下 store/ 文件夹，每个宏一个 JSON 文件 <safe_name>.json，
元数据（作者/说明/下载/上传次数/时间/归属账号）汇总在 store/.index.json。

端点约定（由调用方路由）：
    GET  /api/macros/list     → {ok, count, macros:[...]}
    GET  /api/macros/get?name → {ok, macro}
    POST /api/macros/upload   → {ok, name}
    POST /api/macros/delete   → {ok, name}（需 token，仅归属人可删）
"""

from __future__ import annotations

import json
import os
import re
import time

STORE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "store")
MAX_SIZE = 262144          # 上传宏上限 256KB
MAX_NAME = 40
MAX_DESC = 200
MAX_UID_LEN = 40


def safe_name(name: str) -> str:
    """宏名安全清洗：仅保留汉字/字母/数字/_/-/（）/空格，去路径分隔符，截断。"""
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_（）()\- ]", "", name or "")
    s = re.sub(r"[.\/\\]+", "", s)
    return s[:MAX_NAME].strip()


def _index_path() -> str:
    return os.path.join(STORE_DIR, ".index.json")


def _read_index() -> dict:
    try:
        with open(_index_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_index(idx: dict) -> None:
    os.makedirs(STORE_DIR, exist_ok=True)
    with open(_index_path(), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def list_macros() -> dict:
    """返回共享宏元数据列表（按时间倒序）。"""
    idx = _read_index()
    out = []
    for name, meta in idx.items():
        f = os.path.join(STORE_DIR, safe_name(name) + ".json")
        if not os.path.exists(f):
            continue
        out.append({
            "name": meta.get("name", name),
            "author": meta.get("author", ""),
            "owner": meta.get("owner", ""),
            "type": meta.get("type", "macro"),
            "node": bool(meta.get("node")),
            "desc": meta.get("desc", ""),
            "downloads": int(meta.get("downloads") or 0),
            "uploads": int(meta.get("uploads") or 0),
            "time": meta.get("time", ""),
        })
    out.sort(key=lambda m: m.get("time", ""), reverse=True)
    return {"ok": True, "count": len(out), "macros": out}


def get_macro(name: str) -> dict:
    """下载单个宏；不存在返回 None。"""
    f = os.path.join(STORE_DIR, safe_name(name) + ".json")
    if not os.path.exists(f):
        return None
    with open(f, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        return None
    # 下载计数
    idx = _read_index()
    key = safe_name(name)
    if key in idx:
        idx[key]["downloads"] = int(idx.get(key, {}).get("downloads") or 0) + 1
        _write_index(idx)
    return data


def upload_macro(data: dict) -> tuple[str, str | None]:
    """保存宏；成功返回 (name, None)，失败返回 ("", error)。"""
    if not isinstance(data, dict):
        return "", "数据格式错误"
    raw_size = len(json.dumps(data, ensure_ascii=False))
    if raw_size > MAX_SIZE:
        return "", "宏过大"
    name = safe_name(data.get("name") or "")
    if not name:
        return "", "缺少宏名"
    if not data.get("steps") and not data.get("graph"):
        return "", "缺少宏内容"

    data["name"] = name
    os.makedirs(STORE_DIR, exist_ok=True)
    idx = _read_index()
    prev = idx.get(name, {})
    meta = {
        "name": name,
        "author": (data.get("author") or "")[:20],
        "owner": (data.get("owner") or "")[:MAX_UID_LEN],
        "type": data.get("type") or "macro",
        "node": bool(data.get("graph")),
        "desc": (data.get("desc") or "")[:MAX_DESC],
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "downloads": int(prev.get("downloads") or 0),
        "uploads": int(prev.get("uploads") or 0) + 1,
    }
    idx[name] = meta
    _write_index(idx)
    # 清理下发字段，避免泄露进宏文件
    data.pop("author", None)
    data.pop("owner", None)
    data.pop("desc", None)
    data.pop("type", None)
    try:
        with open(os.path.join(STORE_DIR, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        return "", f"写入失败：{exc}"
    return name, None


def delete_macro(name: str, owner: str) -> tuple[int, str | None]:
    """删除共享宏。仅归属账号（owner）本人可删。成功返回 (200, None)，失败 (code, error)。"""
    key = safe_name(name)
    idx = _read_index()
    meta = idx.get(key)
    if meta is None:
        return 404, "宏不存在"
    if str(meta.get("owner") or "") != owner:
        return 403, "只能删除自己分享的宏"
    f = os.path.join(STORE_DIR, key + ".json")
    try:
        if os.path.exists(f):
            os.remove(f)
    except OSError as exc:
        return 500, f"删除失败：{exc}"
    idx.pop(key, None)
    _write_index(idx)
    return 200, None


def handle_get_macros(query: dict, path: str) -> dict:
    """GET /api/macros/* 路由。返回 (status_code, payload)。"""
    if path == "/api/macros/list":
        return 200, list_macros()
    if path == "/api/macros/get":
        name = (query.get("name") or [""])[0]
        macro = get_macro(name)
        if macro is None:
            return 404, {"ok": False, "error": "宏不存在"}
        return 200, {"ok": True, "macro": macro}
    return 404, {"ok": False, "error": "not found"}


def handle_post_macros(path: str, body: dict) -> tuple[int, dict]:
    """POST /api/macros/* 路由。返回 (status_code, payload)。"""
    if path == "/api/macros/upload":
        name, err = upload_macro(body)
        if err:
            return 400, {"ok": False, "error": err}
        return 200, {"ok": True, "name": name}
    if path == "/api/macros/delete":
        # 删除权限校验：token 对应账号 == 宏归属账号（由调用方传入 owner 已校验）
        code, err = delete_macro(str(body.get("name") or ""), str(body.get("owner") or ""))
        if err:
            return code, {"ok": False, "error": err}
        return 200, {"ok": True, "name": str(body.get("name") or "")}
    return 404, {"ok": False, "error": "not found"}