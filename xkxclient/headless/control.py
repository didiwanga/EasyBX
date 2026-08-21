from __future__ import annotations

import hmac
import json
import threading
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class QtBridge:
    """HTTP 线程 -> Qt 主线程桥。

    控制请求（HTTP 工作线程）把可调用对象排入队列，由 Qt 定时器在事件循环内
    泵出执行。HTTP 线程同步等待结果（带超时）。所有对 QObject/引擎的访问都
    必须经由本桥，保证只在 Qt 主线程触碰 Qt 对象。
    """

    def __init__(self) -> None:
        self._queue: deque = deque()
        self._lock = threading.Lock()

    def post(self, fn):
        """把 fn 排入 Qt 线程执行。返回 (event, result_dict)。"""
        ev = threading.Event()
        res: dict = {}
        with self._lock:
            self._queue.append((fn, ev, res))
        return ev, res

    def pump(self) -> None:
        """由 Qt 定时器周期调用：取出队列中的请求并在 Qt 线程执行。"""
        while True:
            with self._lock:
                if not self._queue:
                    return
                fn, ev, res = self._queue.popleft()
            try:
                res["result"] = fn()
            except Exception:
                traceback.print_exc()
                res["error"] = traceback.format_exc()
            finally:
                ev.set()


class ControlServer:
    """轻量 HTTP 控制服务（stdlib，纯线程）。GET 查状态/输出，POST 下发命令。

    默认绑定 127.0.0.1：配合 SSH 端口转发（ssh -L 8650:127.0.0.1:8650）从本地访问。
    若需公网直连，请在 headless.json 配 token，所有 /api 请求须带
    `Authorization: Bearer <token>` 或 `?token=<token>`。
    """

    def __init__(self, bridge: QtBridge, dispatcher, host: str = "127.0.0.1",
                 port: int = 8650, token: str = "") -> None:
        self.bridge = bridge
        self.dispatcher = dispatcher
        self.host = host
        self.port = port
        self.token = token
        self.httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        class _H(BaseHTTPRequestHandler):

            def log_message(self, *args):  # 静默访问日志
                pass

            def _auth_ok(self) -> bool:
                # 每次读 self.server.token（实例属性）：支持 /api/token 热更新，
                # 不能闭包捕获 start() 时的局部变量（否则改令牌后新令牌被拒、旧令牌永效）
                token = getattr(self.server, "token", "") or ""
                if not token:
                    return True
                hdr = self.headers.get("Authorization") or ""
                if hdr.startswith("Bearer "):
                    given = hdr[7:].strip()
                else:
                    qs = parse_qs(urlparse(self.path).query)
                    given = (qs.get("token") or [""])[0]
                return hmac.compare_digest(given, token)

            def _json(self, code: int, obj) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _html(self, text: str) -> None:
                body = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _bytes(self, code: int, ctype: str, data: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _call(self, fn) -> dict:
                ev, res = self.server.bridge.post(fn)
                if not ev.wait(timeout=10):
                    return {"ok": False, "error": "bridge timeout"}
                if "error" in res:
                    return {"ok": False, "error": res["error"]}
                return {"ok": True, **(res.get("result") or {})}

            def do_GET(self) -> None:
                if self.path.startswith("/api/") and not self._auth_ok():
                    self._json(401, {"ok": False, "error": "unauthorized"})
                    return
                self.server.dispatcher.handle_get(self, self.path)

            def do_POST(self) -> None:
                if self.path.startswith("/api/") and not self._auth_ok():
                    self._json(401, {"ok": False, "error": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    self._json(400, {"ok": False, "error": "bad Content-Length"})
                    return
                if length < 0:
                    self._json(400, {"ok": False, "error": "bad Content-Length"})
                    return
                if length > 1_000_000:  # 1MB 上限：防超大 body 打内存
                    self._json(413, {"ok": False, "error": "payload too large"})
                    return
                raw = self.rfile.read(length).decode("utf-8", "replace") if length else "{}"
                try:
                    data = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    self._json(400, {"ok": False, "error": "bad json"})
                    return
                if not isinstance(data, dict):
                    self._json(400, {"ok": False, "error": "json must be object"})
                    return
                self.server.dispatcher.handle_post(self, self.path, data)

        self.httpd = ThreadingHTTPServer((self.host, self.port), _H)
        self.httpd.bridge = self.bridge
        self.httpd.dispatcher = self.dispatcher
        self.httpd.token = self.token
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None