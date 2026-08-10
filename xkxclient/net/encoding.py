from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def decode_text(data: bytes, encoding: str = "gbk") -> str:
    """带容错解码（wiki A2 编码选择与解码 / E9 编码容错层）。

    优先按配置编码，失败依次回退 utf-8、gbk，仍失败用替换字符。
    """
    candidates = [encoding, "utf-8", "gbk"]
    for enc in candidates:
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode(candidates[-1], errors="replace")


def encode_text(text: str, encoding: str = "gbk") -> bytes:
    try:
        return text.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return text.encode("utf-8")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)