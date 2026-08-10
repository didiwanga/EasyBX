from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QTcpSocket

from xkxclient.net import ansi
from xkxclient.net.telnet import TelnetParser, GMCP_OPT, IAC, DO, SB, SE

CLIENT_ID = "EasyBXb"


class Connection(QObject):
    """TCP + Telnet + 增量解码连接（wiki A4 / A2 / C）。

    信号：
    - connected(host)
    - disconnected(reason)
    - line(spans)        一行，spans = list[ansi.Span]（已着色）
    - gmcp(payload_bytes) GMCP 原始 payload（容错解析在 gmcp 层）
    - error(msg)
    """

    connected = pyqtSignal(str)
    disconnected = pyqtSignal(str)
    line = pyqtSignal(list)
    gmcp = pyqtSignal(bytes)
    error = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sock = QTcpSocket(self)
        self.host = ""
        self.port = 0
        self.encoding = "gbk"
        self._tbuf = bytearray()
        self._parser = TelnetParser(self._reply, self._gmcp_handshake)
        self._gmcp_started = False

        self.sock.connected.connect(lambda: self.connected.emit(self.host))
        self.sock.connected.connect(self._on_connected_probe)
        self.sock.disconnected.connect(lambda: self.disconnected.emit("remote closed"))
        self.sock.readyRead.connect(self._on_ready_read)
        self.sock.errorOccurred.connect(lambda _e: self.error.emit(self.sock.errorString()))

    # ---- 对外 ----
    def open(self, host: str, port: int, encoding: str = "gbk") -> None:
        self.host = host
        self.port = port
        self.encoding = encoding
        self._tbuf = bytearray()
        self._parser = TelnetParser(self._reply, self._gmcp_handshake)
        self._gmcp_started = False
        self.sock.connectToHost(host, port)

    def close(self) -> None:
        self.sock.disconnectFromHost()

    def send_line(self, text: str) -> None:
        data = text.encode(self.encoding, errors="replace") + b"\r\n"
        self.sock.write(data)

    def send_raw(self, data: bytes) -> None:
        self.sock.write(data)

    def start_gmcp_hello(self) -> None:
        """登录完成后补发 GMCP Core.Hello。

        连接早期服务器正处于「输入英文名字」阶段，若立刻发 Core.Hello，
        该文本行会被当成玩家名字输入并报「必须是 3 到 12 个英文字母」。
        因此 GMCP 握手推迟到登录完成后再发；即使服务器未 WILL GMCP，
        连接建立时的 DO 0xC9 主动探测（C1）也会让本补发建立通道。
        """
        self.send_raw(b'Core.Hello {"client":"%s","version":"0.1.0"}\r\n' % CLIENT_ID.encode("utf-8"))

    # ---- 内部 ----
    def _reply(self, data: bytes) -> None:
        self.sock.write(data)

    def _on_connected_probe(self) -> None:
        """C1：连接建立后主动补发一次 DO 0xC9（即使服务器没先 WILL GMCP）。

        纯 IAC 字节不构成文本输入，不会打扰登录阶段；配合登录完成后的
        Core.Hello 建立 GMCP 通道。
        """
        self.send_raw(bytes([IAC, DO, GMCP_OPT]))

    def _gmcp_handshake(self) -> None:
        if self._gmcp_started:
            return
        self._gmcp_started = True
        # C1：服务器 WILL GMCP 后不立即发 Core.Hello（避免被当成登录输入），
        # DO 应答由 TelnetParser 完成，Core.Hello 由登录完成后的 start_gmcp_hello() 补发。

    def _on_ready_read(self) -> None:
        data = bytes(self.sock.readAll())
        text = self._parser.feed(data)
        self._tbuf += text
        # 按 \n 切行（含 \r\n 归一化）
        while b"\n" in self._tbuf:
            raw, self._tbuf = bytes(self._tbuf).split(b"\n", 1)
            raw = raw.rstrip(b"\r")
            spans = ansi.decode_runs(raw, self.encoding)
            if spans:
                self.line.emit(spans)
        # IAC GA：服务器用 GA 表示提示符结束（无换行），立即交付缓冲文本
        if self._parser.ga_seen:
            self._parser.ga_seen = False
            if self._tbuf:
                raw = bytes(self._tbuf)
                self._tbuf = bytearray()
                spans = ansi.decode_runs(raw, self.encoding)
                if spans:
                    self.line.emit(spans)
        for payload in self._parser.take_gmcp():
            self.gmcp.emit(payload)