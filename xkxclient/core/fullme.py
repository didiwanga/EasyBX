from __future__ import annotations

import re

# fullme 链接形如 `http://fullme.pkuxkx.net/m.php?username=xx` 或含 `antirobot/robot`。
# 关键词可能出现在路径开头（fullme.pkuxkx.net），故不能要求其前必有字符。
_URL_RE = re.compile(
    r"https?://[^\s\"'<>]*(?:fullme|antirobot|robot)[^\s\"'<>]*"
)


def extract_fullme_url(text: str) -> str | None:
    m = _URL_RE.search(text)
    return m.group(0) if m else None


def is_fullme_related(text: str) -> bool:
    return bool(_URL_RE.search(text)) or ("fullme" in text and "验证" not in text)