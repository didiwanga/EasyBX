from __future__ import annotations

from typing import Callable

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
GMCP_OPT = 0xC9


class TelnetParser:
    """Telnet IAC 解析器（wiki A3 / C-GMCP C1）。

    - 剥离 IAC 命令，返回纯文本字节（仍含 ANSI 转义，交给 ansi 层）。
    - WILL 0xC9(GMCP) → 回 DO 并回调 gmcp_handshake() 发 Core.Hello。
    - SB GMCP 子协商 → 累积原始 payload 字节，由 gmcp 层做容错 JSON。
    """

    def __init__(self, on_reply: Callable[[bytes], None], on_gmcp_handshake: Callable[[], None]) -> None:
        self.on_reply = on_reply
        self.on_gmcp_handshake = on_gmcp_handshake
        self.buf = bytearray()
        self.gmcp_queue: list[bytes] = []
        self.ga_seen = False

    def feed(self, data: bytes) -> bytes:
        self.buf += data
        out = bytearray()
        i = 0
        n = len(self.buf)
        while i < n:
            c = self.buf[i]
            if c == IAC:
                if i + 1 >= n:
                    break
                cmd = self.buf[i + 1]
                if cmd in (DO, DONT, WILL, WONT):
                    if i + 2 >= n:
                        break
                    opt = self.buf[i + 2]
                    if cmd == WILL:
                        if opt == GMCP_OPT:
                            self.on_reply(bytes([IAC, DO, opt]))
                            self.on_gmcp_handshake()
                        # 其余选项暂不回（避免服务器行为变化）
                    i += 3
                elif cmd == SB:
                    j = i + 2
                    while j < n - 1 and not (self.buf[j] == IAC and self.buf[j + 1] == SE):
                        j += 1
                    if j >= n - 1:
                        break
                    sb = bytes(self.buf[i + 2 : j])
                    if sb and sb[0] == GMCP_OPT:
                        self.gmcp_queue.append(sb[1:])
                    i = j + 2
                elif cmd == 0xF9:  # IAC GA：提示符结束（无换行），立即交付缓冲文本
                    self.ga_seen = True
                    i += 2
                else:
                    i += 2
            else:
                out.append(c)
                i += 1
        self.buf = self.buf[i:] if i < n else bytearray()
        return bytes(out)

    def take_gmcp(self) -> list[bytes]:
        q = self.gmcp_queue
        self.gmcp_queue = []
        return q