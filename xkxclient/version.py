"""EasyBXb 版本与更新清单的唯一来源。

所有需要显示/发送版本号的地方（关于窗口、托盘、GMCP Core.Hello、
Lua 绑定、自动更新）都必须从这里读取，禁止在别处硬编码版本号。
"""

VERSION = "1.2.35"

# 版本更新记录（追加式：最新在前，过往往后排；部署时写入服务器清单 changelog 字段）
CHANGELOG: list[dict] = [
    {
        "v": "1.2.35", "date": "2026-08-16",
        "items": [
            "测试发布：验证 v1.2.34 完整更新流程（弹窗→下载→准备更新→替换→重启）",
        ],
    },
    {
        "v": "1.2.34", "date": "2026-08-16",
        "items": [
            "彻底修复下载进度到 100% 后无下文：移除 _cancelled 取消拦截逻辑（QProgressDialog 100% 时触发 canceled 被误判为用户取消导致跳过），改用自建进度对话框，从根上消除该问题",
        ],
    },
    {
        "v": "1.2.33", "date": "2026-08-16",
        "items": [
            "测试发布：验证 v1.2.32 完整更新流程（弹窗→下载→准备更新→替换→重启）",
        ],
    },
    {
        "v": "1.2.32", "date": "2026-08-16",
        "items": [
            "修复 fullme/宏/红包验证码窗口输入验证码确认后误报「请先输入验证码」：提交前强制提交输入法预编辑内容、等待结果期间禁止重复提交",
            "发现玩家：命中行整行高亮、命中提示音（默认开启）、支持 ; 分隔多条指令、去掉示例文案",
        ],
    },
    {
        "v": "1.2.31", "date": "2026-08-16",
        "items": [
            "测试发布：验证 v1.2.30 完整更新流程（弹窗→下载→准备更新→替换→重启）",
        ],
    },
    {
        "v": "1.2.30", "date": "2026-08-16",
        "items": [
            "彻底修复下载进度到 100% 后无下文：去掉进度条取消按钮（cancelButtonText 为空），杜绝 canceled 信号误判为用户取消",
        ],
    },
    {
        "v": "1.2.29", "date": "2026-08-16",
        "items": [
            "测试发布：验证 v1.2.28 完整更新流程（弹窗→下载→准备更新→替换→重启）",
        ],
    },
    {
        "v": "1.2.28", "date": "2026-08-16",
        "items": [
            "修复下载进度到 100% 后无下文：QProgressDialog 自动重置触发 canceled 被误判为用户取消，禁用 autoReset/autoClose 并增加完成保护",
        ],
    },
    {
        "v": "1.2.27", "date": "2026-08-16",
        "items": [
            "测试发布：验证 v1.2.26 完整更新流程（弹窗→下载→确认→替换→重启）",
        ],
    },
    {
        "v": "1.2.26", "date": "2026-08-16",
        "items": [
            "修复更新确认框点「开始更新」无反应：QMessageBox 自定义按钮的 finished 返回值非标准 DialogCode，改用按钮对象判断",
        ],
    },
    {
        "v": "1.2.25", "date": "2026-08-16",
        "items": [
            "测试发布：验证 v1.2.24 更新弹窗与下载流程正常",
        ],
    },
    {
        "v": "1.2.24", "date": "2026-08-16",
        "items": [
            "修复更新提示弹窗崩溃：改为非阻塞对话框（原在 Qt 网络信号回调内嵌套事件循环，点击「立即更新」时 Qt6 崩溃闪退）",
        ],
    },
    {
        "v": "1.2.23", "date": "2026-08-16",
        "items": [
            "修复更新提示弹窗在个别版本（漏导入 QWidget）下弹出即闪退的问题",
        ],
    },
    {
        "v": "1.2.22", "date": "2026-08-16",
        "items": [
            "宏新增「巡航」步骤：按八方向范围（& 连接多条路径）顺序或随机巡航，每位置点可设触发条件/执行命令/延时/条件超时，命中后可选择返回起点执行、执行后返回、仅执行、仅返回；全程可设总超时",
            "新增「发现玩家」功能（工具栏按钮）：监控服务器信息，发现配置的玩家（中文名(英文名)）即触发设定指令，指令中 <cn>/<en> 分别引用中文名/英文名（英文发送时全小写）",
            "修复主输出在大量文本时清屏/拖动滑块崩溃闪退（滚动回调内不再直接改文档，批量加载/裁剪历史）",
            "验证码窗口留空时不允许确认（回车/按钮均拦截并提示），防止误发空指令",
            "移动控制 dock 的 3×3 方向按钮绑定小键盘 1-9 快捷键",
            "代码全面审查修复：别名 %N 替换顺序、GMCP 双发布、注册/无密码登录状态、定时器跨线程、Lua 脚本只读目录写入、更新器取消误报等",
        ],
    },
    {
        "v": "1.2.21", "date": "2026-08-16",
        "items": [
            "触发器编辑器精简：去掉顶层「匹配类型/模式」，统一用条件列表",
            "触发器、宏「等待输入」、宏「验证码」新增声音提醒（命中/弹窗时播放「叮」，默认关闭）",
            "刷新 walk 列表时若服务器返回「内建路径出发点不明确」立即终止读取，不再等超时",
            "更新提示弹窗显示更新内容（最新在最上方，过往版本往后排）",
        ],
    },
    {
        "v": "1.2.20", "date": "2026-08-16",
        "items": [
            "菜单重构：全部带 emoji，设置合并到统一窗口（通用/字体/布局/编码/快捷键）",
            "功能面板新默认布局：左右分列，命令速查/DSL 手册默认隐藏、打开悬浮",
            "修复 walk 列表读取（改为 walk -c）",
            "修复连接崩溃（session.py 缩进损坏）",
        ],
    },
]

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