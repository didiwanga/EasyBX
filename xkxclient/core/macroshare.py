"""宏分享：上传本地宏到服务器 / 从服务器浏览下载共享宏。

服务器接口约定（server/macros/api.php）：
    GET  ?action=list                → {ok, count, macros:[{name,author,type,node,desc,downloads,time}]}
    GET  ?action=get&name=<name>     → {ok, macro}
    POST ?action=upload              → body=宏JSON {name,author,desc,type,steps|graph,...}
"""

from __future__ import annotations

import json
import urllib.request
import urllib.parse
import urllib.error

from xkxclient.version import (
    MACRO_SHARE_LIST_URL, MACRO_SHARE_GET_URL, MACRO_SHARE_UPLOAD_URL,
)

_TIMEOUT = 15


def _opener():
    # 公网直连（与 fullme/地图 API 一致），避免系统代理干扰
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def share_url() -> str:
    return MACRO_SHARE_URL


class ShareError(RuntimeError):
    """宏分享网络/服务器错误（HTTP 非 2xx 或响应异常）。"""


def _read_json(req) -> dict:
    """发请求并解析 JSON；HTTP 错误/网络错误/非 JSON 响应统一抛 ShareError。"""
    try:
        with _opener().open(req, timeout=_TIMEOUT) as r:
            raw = r.read()
            text = raw.decode("utf-8-sig", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8-sig", errors="replace")
        msg = ""
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict) and parsed.get("error"):
                msg = parsed["error"]
        except (ValueError, TypeError):
            pass
        raise ShareError(msg or f"服务器返回 HTTP {exc.code}") from exc
    except OSError as exc:
        raise ShareError(f"网络错误：{exc}") from exc
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ShareError("服务器响应不是有效 JSON（接口可能未部署/PHP 未生效）") from exc
    if not isinstance(data, dict) or not data.get("ok"):
        raise ShareError(str(data.get("error") or "服务器返回异常"))
    return data


def list_remote_macros() -> list[dict]:
    """拉取服务器全部共享宏元数据列表。失败抛异常。"""
    data = _read_json(MACRO_SHARE_LIST_URL)
    return data.get("macros") or []


def fetch_macro(name: str) -> dict:
    """下载单个宏完整 JSON。失败抛异常。"""
    data = _read_json(MACRO_SHARE_GET_URL + "?name=" + urllib.parse.quote(name))
    macro = data.get("macro")
    if not isinstance(macro, dict):
        raise ShareError("宏数据异常")
    return macro


def upload_macro(macro: dict) -> str:
    """上传宏到服务器（覆盖同名）。成功返回宏名。"""
    body = json.dumps(macro, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(MACRO_SHARE_UPLOAD_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    data = _read_json(req)
    return str(data.get("name") or "")