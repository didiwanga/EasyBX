#!/usr/bin/env python3
"""EasyBXb API 服务器：宏分享 + 客户端用户云同步（纯标准库，无地图功能）。

由原 map_server_v3.py 拆分而来（2026-08-17 地图功能全清，宏/用户保留）：
    GET  /api/user/list             → 客户端账号列表
    POST /api/user/register|login|settings/*|automation/*  → 用户云同步
    GET  /api/macros/list|get       → 宏分享列表/下载
    POST /api/macros/upload|delete  → 宏分享上传/删除（delete 需 token）

部署: systemd + nginx（同原 map_server；/api/macros、/api/user 仍反代到本服务）
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# 兼容两种目录布局：部署目录平铺 或 本地 server/macros/ 子目录
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from macro_server import handle_get_macros, handle_post_macros
except ImportError:
    from macros.macro_server import handle_get_macros, handle_post_macros
from user_server import handle_get_user, handle_post_user, _check_token

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5001


class Handler(BaseHTTPRequestHandler):
    server_version = "EasyBXbApi/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)
        if path == "/api/user/list":
            code, payload = handle_get_user(q, path)
            self.send_json(payload, code=code)
            return
        if path.startswith("/api/macros/"):
            code, payload = handle_get_macros(q, path)
            self.send_json(payload, code=code)
            return
        self.send_json({"error": "not found"}, code=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "bad json"}, code=400)
            return
        if path == "/api/user/register" or path == "/api/user/login" or path.startswith("/api/user/"):
            code, payload = handle_post_user(path, body)
            self.send_json(payload, code=code)
            return
        if path == "/api/macros/delete":
            # 删除宏归属校验：token 对应账号 == 宏归属
            token = str(body.get("token") or "")
            uid = _check_token(token)
            if uid is None:
                self.send_json({"ok": False, "error": "登录已失效，请重新登录"}, code=401)
                return
            body["owner"] = uid
            code, payload = handle_post_macros(path, body)
            self.send_json(payload, code=code)
            return
        if path.startswith("/api/macros/"):
            code, payload = handle_post_macros(path, body)
            self.send_json(payload, code=code)
            return
        self.send_json({"error": "not found"}, code=404)

    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))


def main():
    print(f"[api_server] listening on 0.0.0.0:{PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()