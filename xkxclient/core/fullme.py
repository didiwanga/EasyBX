from __future__ import annotations

import re

_URL_RE = re.compile(
    r"(https?://[^\s\"'<>]+(?:(?:fullme|antirobot|robot)[\w/\.=&?%\-]*))"
)


def extract_fullme_url(text: str) -> str | None:
    m = _URL_RE.search(text)
    return m.group(1) if m else None


def is_fullme_related(text: str) -> bool:
    return bool(_URL_RE.search(text)) or ("fullme" in text and "验证" not in text)