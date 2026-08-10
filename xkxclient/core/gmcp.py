from __future__ import annotations

import json
import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_DOUBLE_RE = re.compile(r"[\u0080-\u00ff]")


def clean_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def fix_double(s: str) -> str:
    """修复「GBK 被当 UTF-8/字节」的双重编码（wiki E9）。

    当字符串内含 latin-1 高位字符(0x80-0xff)时，将原 GBK 字节还原：
    lstripped_latin1 → encode latin1 → decode gbk。
    """
    if not _DOUBLE_RE.search(s):
        return s
    try:
        return s.encode("latin-1").decode("gbk", errors="replace")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _fix_value(v):
    if isinstance(v, str):
        v = clean_ansi(v)
        return fix_double(v)
    if isinstance(v, list):
        for i, item in enumerate(v):
            v[i] = _fix_value(item)
    elif isinstance(v, dict):
        for k, item in v.items():
            v[k] = _fix_value(item)
    return v


def parse_json_tolerant(data: bytes) -> dict | list | None:
    """GMCP payload 容错解析（wiki E9 / C-GMCP C2）。

    字节→latin1 → 去 ANSI → json.loads → 对字符串做双重编码修复。
    偶发整个 JSON 被嵌套成 {"raw": "..."} 时二次解析。
    """
    if not data:
        return None
    text = data.decode("latin-1")
    text = _ANSI_RE.sub("", text)
    obj = _try_json(text)
    if obj is None:
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        obj = _try_json(cleaned)
    # 双重编码：`{"raw": "{...}"}` 整个 JSON 被嵌套，需二次解析
    if isinstance(obj, dict) and len(obj) == 1 and isinstance(obj.get("raw"), str):
        inner = _try_json(obj["raw"])
        if inner is not None:
            obj = inner
    return _fix_value(obj)


def _try_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(text, strict=False)


def parse_payload(payload: bytes) -> tuple[str, dict | list | None]:
    """切分 GMCP 报文 `模块<space>JSON` → (module, data)。"""
    if b" " in payload:
        module, rest = payload.split(b" ", 1)
    else:
        module, rest = payload, b""
    return module.decode("ascii", errors="replace"), parse_json_tolerant(rest)