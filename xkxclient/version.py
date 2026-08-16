"""EasyBXb 版本与更新清单的唯一来源。

所有需要显示/发送版本号的地方（关于窗口、托盘、GMCP Core.Hello、
Lua 绑定、自动更新）都必须从这里读取，禁止在别处硬编码版本号。
"""

VERSION = "1.2.20"

# 更新服务器：阿里云 pytools.cloud（同一文件也通过 http://47.104.0.91 提供）
UPDATE_BASE = "http://pytools.cloud"
UPDATE_MANIFEST_URL = UPDATE_BASE + "/EasyBXb_version.json"
UPDATE_DOWNLOAD_URL = UPDATE_BASE + "/EasyBXb.exe"

# 宏分享接口（nginx 反代到服务器 map_server 的 /api/macros/*）
MACRO_SHARE_BASE = UPDATE_BASE + "/api/macros"
MACRO_SHARE_LIST_URL = MACRO_SHARE_BASE + "/list"
MACRO_SHARE_GET_URL = MACRO_SHARE_BASE + "/get"
MACRO_SHARE_UPLOAD_URL = MACRO_SHARE_BASE + "/upload"
MACRO_SHARE_DELETE_URL = MACRO_SHARE_BASE + "/delete"

# 客户端账号接口（同服务器 /api/user/*）
USER_API_BASE = UPDATE_BASE + "/api/user"
USER_REGISTER_URL = USER_API_BASE + "/register"
USER_LOGIN_URL = USER_API_BASE + "/login"
USER_SETTINGS_UPLOAD_URL = USER_API_BASE + "/settings/upload"
USER_SETTINGS_DOWNLOAD_URL = USER_API_BASE + "/settings/download"
USER_AUTOMATION_UPLOAD_URL = USER_API_BASE + "/automation/upload"
USER_AUTOMATION_DOWNLOAD_URL = USER_API_BASE + "/automation/download"


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