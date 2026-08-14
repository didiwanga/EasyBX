"""EasyBXb 版本与更新清单的唯一来源。

所有需要显示/发送版本号的地方（关于窗口、托盘、GMCP Core.Hello、
Lua 绑定、自动更新）都必须从这里读取，禁止在别处硬编码版本号。
"""

VERSION = "1.2.4"

# 更新服务器：阿里云 pytools.cloud（同一文件也通过 http://47.104.0.91 提供）
UPDATE_BASE = "http://pytools.cloud"
UPDATE_MANIFEST_URL = UPDATE_BASE + "/EasyBXb_version.json"
UPDATE_DOWNLOAD_URL = UPDATE_BASE + "/EasyBXb.exe"


def parse_version(text: str) -> tuple:
    """把 '1.2.0' / 'v1.2.0' 之类解析为可比较的整数元组。

    不合法/空输入返回 (0,)，保证任何比较都安全。
    """
    import re

    m = re.match(r"[vV]?\s*([\d.]+)", str(text or "").strip())
    if not m:
        return (0,)
    return tuple(int(p) for p in m.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    """latest 是否比 current 更新（按数字段逐级比较）。"""
    return parse_version(latest) > parse_version(current)