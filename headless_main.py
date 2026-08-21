import argparse
import json
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(prog="easybxb-headless", description="EasyBXb 无头守护进程")
    ap.add_argument("--root", default="data",
                    help="配置根目录（APPDATA 语义，配置落在 <root>/XkxClient）")
    ap.add_argument("--config", default=None,
                    help="headless.json 路径（默认 <root>/XkxClient/headless.json）")
    ap.add_argument("--control-host", default="127.0.0.1", help="控制 HTTP 监听地址（默认仅本机）")
    ap.add_argument("--control-port", type=int, default=8650, help="控制 HTTP 端口（默认 8650）")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    # 必须在导入 xkxclient 前设置：ConfigManager/HistoryStore/Connection 均以 APPDATA 为根
    os.environ["APPDATA"] = root

    config_path = args.config or os.path.join(root, "XkxClient", "headless.json")
    if not os.path.exists(config_path):
        print(f"缺少配置文件: {config_path}", file=sys.stderr)
        print("示例：", file=sys.stderr)
        print(json.dumps({
            "token": "",
            "server": {"host": "mud.pkuxkx.net", "port": 8080, "encoding": "gbk"},
            "accounts": [
                {"id": "acct1", "username": "acct1", "password": "pwd",
                 "init_cmds": [], "autologin": True}
            ],
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    from PyQt6.QtCore import QCoreApplication

    from xkxclient.headless.daemon import HeadlessDaemon

    qapp = QCoreApplication(sys.argv)
    daemon = HeadlessDaemon(root, config_path, args.control_host, args.control_port)
    daemon.control.start()

    def _term(_sig, _frame) -> None:
        daemon.shutdown()
        qapp.quit()

    import signal

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    print(f"[headless] 控制台: http://{args.control_host}:{args.control_port}  "
          f"（本地用 ssh -L {args.control_port}:127.0.0.1:{args.control_port} "
          f"root@服务器 转发后访问）", flush=True)
    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())