from __future__ import annotations

from dataclasses import dataclass

_BASIC = [
    "000000", "800000", "008000", "808000", "000080", "800080", "008080", "c0c0c0",
    "808080", "ff0000", "00ff00", "ffff00", "0000ff", "ff00ff", "00ffff", "ffffff",
]


def _color256(n: int) -> str:
    if n < 16:
        return _BASIC[n]
    if n < 232:
        n -= 16
        r, g, b = n // 36, (n % 36) // 6, n % 6
        r = [0, 95, 135, 175, 215, 255][r]
        g = [0, 95, 135, 175, 215, 255][g]
        b = [0, 95, 135, 175, 215, 255][b]
        return f"{r:02x}{g:02x}{b:02x}"
    v = 8 + (n - 232) * 10
    return f"{v:02x}{v:02x}{v:02x}"


def _sgr_fg(params: list[int]) -> str | None:
    if not params:
        return None
    code = params[0]
    if 30 <= code <= 37:
        return _BASIC[code - 30]
    if 90 <= code <= 97:
        return _BASIC[code - 90 + 8]
    if code == 38 and len(params) >= 3:
        if params[1] == 5 and len(params) >= 3:
            return _color256(params[2])
        if params[1] == 2 and len(params) >= 5:
            r, g, b = params[2], params[3], params[4]
            return f"{r:02x}{g:02x}{b:02x}"
    return None


def _sgr_bg(params: list[int]) -> str | None:
    if not params:
        return None
    code = params[0]
    if 40 <= code <= 47:
        return _BASIC[code - 40]
    if 100 <= code <= 107:
        return _BASIC[code - 100 + 8]
    if code == 48 and len(params) >= 3:
        if params[1] == 5 and len(params) >= 3:
            return _color256(params[2])
        if params[1] == 2 and len(params) >= 5:
            r, g, b = params[2], params[3], params[4]
            return f"{r:02x}{g:02x}{b:02x}"
    return None


@dataclass
class Span:
    text: str
    fg: str | None = None
    bg: str | None = None
    bold: bool = False


def decode_runs(data: bytes, encoding: str = "gbk") -> list[Span]:
    """字节级剥离 ANSI 并解码，返回 (文本, 前景色, 背景色, 加粗) 分段。

    先于 GBK 解码剥离转义，避免转义字节破坏多字节字符（A2/E9 增量解码）。
    """
    spans: list[Span] = []
    if not data:
        return spans
    byte_runs: list[tuple[bytes, str | None, str | None, bool]] = []
    cur = bytearray()
    fg: str | None = None
    bg: str | None = None
    bold = False
    i = 0
    n = len(data)
    while i < n:
        c = data[i]
        if c == 0x1B:
            if i + 1 < n and data[i + 1] == 0x5B:  # CSI
                j = i + 2
                while j < n and not (0x40 <= data[j] <= 0x7E):
                    j += 1
                if j >= n:
                    break
                final = data[j]
                # 先冲刷当前累积文本（使用旧颜色），再应用 SGR，
                # 否则颜色码之前的文本会被错误染上新颜色。
                byte_runs.append((bytes(cur), fg, bg, bold))
                cur = bytearray()
                if final == ord("m"):
                    params: list[int] = []
                    seg = data[i + 2 : j].split(b";")
                    for p in seg:
                        try:
                            params.append(int(p))
                        except ValueError:
                            params.append(0)
                    k = 0
                    while k < len(params):
                        p = params[k]
                        if p == 0:
                            fg, bg, bold = None, None, False
                        elif p == 1:
                            bold = True
                        elif p == 22:
                            bold = False
                        elif 30 <= p <= 37 or 90 <= p <= 97:
                            fg = _sgr_fg(params[k:])
                        elif p == 38:
                            fg = _sgr_fg(params[k:])
                            k += 2 if (k + 1 < len(params) and params[k + 1] == 5) else 4
                        elif p == 39:
                            fg = None
                        elif 40 <= p <= 47 or 100 <= p <= 107:
                            bg = _sgr_bg(params[k:])
                        elif p == 48:
                            bg = _sgr_bg(params[k:])
                            k += 2 if (k + 1 < len(params) and params[k + 1] == 5) else 4
                        elif p == 49:
                            bg = None
                        k += 1
                i = j + 1
                continue
            else:
                i += 2
                continue
        else:
            cur.append(c)
            i += 1
    if cur:
        byte_runs.append((bytes(cur), fg, bg, bold))
    for raw, f, b, bo in byte_runs:
        if not raw:
            continue
        spans.append(Span(raw.decode(encoding, errors="replace"), f, b, bo))
    return spans