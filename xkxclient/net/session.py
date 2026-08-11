from __future__ import annotations

import json
import re
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from xkxclient.core import gmcp
from xkxclient.core.config import ConfigManager
from xkxclient.core.dsl import DslEngine
from xkxclient.core.fullme import extract_fullme_url
from xkxclient.core.history import HistoryStore
from xkxclient.core.map import MapCache
from xkxclient.core.skills import _GROUP_RE as _SKILL_GROUP_RE
from xkxclient.core.skills import _SKILL_ROW_RE as _SKILL_ROW_RE
from xkxclient.core.state import CharacterState
from xkxclient.net.connection import Connection
from xkxclient.parse.look import LookParser

_GMCP_SUBSCRIBE = ["Status", "Move", "System", "Combat", "Buff"]

# 「命令进入缓冲」提示（服务端命令缓冲限流，见 wiki about_cmdbuffer）
_BUF_PROMPTS = ("命令进入缓冲", "命令缓冲", "指令进入缓冲")

# node 表格框线字符：捕获期间仅抑制含这些字符的行，其余信息照常上屏
_NODE_BOX_CHARS = "│┌┐└┘├┤─"
_NODE_EMPTY_MSG = "这里没有玩家定义的路径"
# node 数据行兜底：`[│|]?[★☆]?ASCII名称│`（表头页丢失时也能进表）
_NODE_ROW_RE = re.compile(r"^\s*[│|]?\s*[★☆]?\s*[a-zA-Z_][a-zA-Z0-9_]*\s*[│|]")
# node 表格分页自动继续（持续看门狗）。服务器每页约 40 行内容 + 一条
# 「==未完继续==」提示行（晚约 1s 发送，且不占页）。正常流程由 `_is_pager`
# 识别提示行翻页；但提示行可能漏识别/迟到，且各页内容行数并不总是对齐 40，
# 旧的「行数>=40 才启动 1.5s 兜底」会在错误时机抢发空指令、被迟到的提示行
# 吞掉，导致服务器分页一直停在等待输入、屏幕冻结。改为持续看门狗：
#   进表即启动；每次收到表格行/提示行都刷新时间戳并重排定时器；
#   超过 _NODE_PAGE_GAP 秒没有表格活动 → 判定服务器等翻页输入 → 自动补发
#   空指令；翻页生效收到新行后复位；多次补发仍无进展则停止探测。
_NODE_PAGE_GAP = 2.0          # 表格停摆多少秒判定为等待分页输入（>提示行 ~1s 延迟）
_NODE_PAGE_RETRY_MAX = 4      # 单次停摆最多补发几次空指令，之后静默等超时收尾
# 自动翻页后：连续收到这么多非表格行 ⇒ 判定 node 已无数据，关闭捕获。
_NODE_STRAY_MAX = 2

# 通用分页看门狗（非 node 命令，如 help list）：一旦识别到 `== 未完继续 ==`
# 提示行即进入分页会话，此后内容/提示行停摆超过 _PAGER_GAP 秒仍无进展 →
# 判定服务器分页在等输入，自动补发翻页；补发仍无进展则按上限重试后放弃。
# 结束条件：裸 `> ` 提示行（服务器已回到正常命令环）、用户新发命令、或重试封顶。
# 间隔取 1.5s：大于提示行晚于页内容 ~1s 的典型延迟（正常流程不会误触），
# 又足够快地兜住「首次回车未生效」的情况（实测 help list 即需补发）。
_PAGER_GAP = 1.5
_PAGER_RETRY_MAX = 4

DEFAULT_PROMPTS = {
    "encoding": ("Input 1 for GBK", "Input 1 for UTF8", "编码已改为"),
    "name": ("英文名字",),
    "password": ("请输入密码", "密码"),
    "mxp": ("<SUPPORT>",),
    "ready": ("> ",),
}

# 名字提示可能有两种形式：
# - 「您的英文名字：」（普通登录）
# - 「您的英文名字（要注册新人物请输入new。）：编码已改为GBK。」（注册提示行，编码确认与提示合并在一行）
# 均需识别为名字提示。
_REAL_NAME_RE = re.compile(r"您的英文名字(?:\（[^）]*\）)?\s*：")


class AccountSession(QObject):
    """每账号运行时实例（wiki D4）：连接 + 状态 + 自动化引擎 + D2 自动登录。"""

    line_displayed = pyqtSignal(list, bool)     # 应进入主输出的文本, 是否触发器命中高亮
    channel_text = pyqtSignal(str, list, bool)  # channel name, spans(含色) → 进聊天栏, 是否高亮

    def __init__(self, app, account_id: str) -> None:
        super().__init__(app)
        self.app = app
        self.account_id = account_id
        self.connection = Connection(self)
        self.state = CharacterState()
        self.room_name = ""
        self.exits: list[str] = []
        self.connected = False
        self.logged_in = False
        self.vars: dict = {}
        self.last_line = ""
        self.chat_open = True  # B5e：聊天栏恒开
        self._fullme_collect = 0
        self._fullme_urls: list[str] = []
        self._macro_captcha_active = False  # 宏验证码步骤期间抑制普通 fullme 弹窗

        from xkxclient.automation.alias import AliasEngine
        from xkxclient.automation.combat import CombatEngine
        from xkxclient.automation.macro import MacroEngine
        from xkxclient.automation.throttle import CommandThrottle
        from xkxclient.automation.timer import TimerEngine
        from xkxclient.automation.trigger import TriggerEngine

        self.triggers = TriggerEngine(app.bus, self)
        self.aliases = AliasEngine(self)
        self.timers = TimerEngine(app.bus, self)
        self.macros = MacroEngine(app.bus, self)
        self.combat = CombatEngine(self)
        self.throttle = CommandThrottle(self.connection, self.account_id, app.bus)
        # B9 全局开关：读配置初值
        self.triggers.master_on = bool(ConfigManager.instance().get("automation.trigger_on", True))
        self.timers.master_on = bool(ConfigManager.instance().get("automation.timer_on", True))

        self.dsl = DslEngine(self)
        self.history = HistoryStore(account_id)
        self.look_parser = LookParser(app.bus)
        self.map_cache = MapCache(app.bus, account_id, self)
        from xkxclient.core.map import LookCapture, Navigator
        self.look_capture = LookCapture(app.bus, self.map_cache, self)
        self.navigator = Navigator(self.map_cache, self, app.bus, self)
        self.auto_look = ConfigManager.instance().get("map.auto_look", False)

        # 技能/look 文本捕获
        self._capture_skills = False
        self._skills_buf = ""
        self.skills_dock = None
        self._capture_look = False
        self._look_buf = ""

        # node 命令捕获：表格行拦截不上主输出（仅表格行），带 5s 超时兜底
        self._node_capture = False
        self._node_capture_start = 0.0
        self._node_in_table = False
        # 分页自动继续（持续看门狗，见 _NODE_PAGE_GAP）
        self._node_page_lines = 0      # 表内累计行数（数据行+分隔线）
        self._node_page_sent = False   # 当前页是否已发过自动翻页
        self._node_stray = 0           # 自动翻页后的连续杂行计数
        self._node_page_last_active = 0.0   # 上次表格/提示行活动时间（monotonic）
        self._node_page_retries = 0         # 当前停摆已补发空指令次数
        self._node_page_timer = QTimer(self)
        self._node_page_timer.setSingleShot(True)
        self._node_page_timer.setInterval(int(_NODE_PAGE_GAP * 1000))
        self._node_page_timer.timeout.connect(self._node_page_fallback)

        # 通用分页会话（持续看门狗，见 _PAGER_GAP）
        self._pager_pending = False      # 通用分页会话进行中（已识别过提示行）
        self._pager_last = 0.0           # 上次会话活动时间（monotonic）
        self._pager_retries = 0          # 当前停摆已补发次数
        self._pager_timer = QTimer(self)
        self._pager_timer.setSingleShot(True)
        self._pager_timer.setInterval(int(_PAGER_GAP * 1000))
        self._pager_timer.timeout.connect(self._pager_fallback)

        # 频道开关（B5e 持久化：config "channels"）
        self._channels: dict[str, bool] = dict(ConfigManager.instance().get("channels") or {})

        # 重连（A4）
        self.auto_reconnect = bool(ConfigManager.instance().get("net.auto_reconnect", True))
        self._manual_close = False
        self._connect_args: dict = {}
        self._reconnect_timer: QTimer | None = None
        self._reconnect_attempt = 0
        self._last_sent = ""

        self._login = None
        self._load_automation()

        # B3b 5b' 宏录制：由 MacroRecorderDock 注入，录制用户手动输入命令
        self._macro_recorder = None
        self._macro_recording = False

        self.connection.connected.connect(self._on_connected)
        self.connection.disconnected.connect(self._on_disconnected)
        self.connection.line.connect(self._on_line)
        self.connection.gmcp.connect(self._on_gmcp)
        self.connection.error.connect(lambda msg: self.app.bus.publish("net.error", account=self.account_id, msg=msg))

    # ---- 连接 ----
    def connect_to(self, host: str, port: int, encoding: str = "gbk",
                   username: str | None = None, password: str | None = None,
                   init_cmds: list[str] | None = None, autologin: bool = True,
                   register: bool = False) -> None:
        self._connect_args = dict(host=host, port=port, encoding=encoding, username=username,
                                  password=password, init_cmds=init_cmds or [], autologin=autologin,
                                  register=register)
        self._manual_close = False
        self._reconnect_attempt = 0
        self._login = LoginMachine(self, username, password, self._connect_args["init_cmds"],
                                   autologin, register)
        self.connection.open(host, port, encoding)

    def _load_automation(self) -> None:
        cfg = ConfigManager.instance()
        defs = cfg.automation(self.account_id)
        self.triggers.load(defs.get("triggers", []))
        self.aliases.load(defs.get("aliases", []))
        self.timers.load(defs.get("timers", []))
        self.macros.load(defs.get("macros", []))

    def reload_automation(self) -> None:
        self._load_automation()

    def set_encoding(self, encoding: str) -> None:
        self.connection.encoding = encoding

    def _on_connected(self, host: str) -> None:
        self.connected = True
        # 重连后恢复定时器（interval 由 _schedule_all 重启，日历式由 tick 恢复）
        self.timers.load(list(ConfigManager.instance().automation(self.account_id).get("timers", [])))
        self.app.bus.publish("net.connected", account=self.account_id, host=host)

    def _on_disconnected(self, reason: str) -> None:
        self.connected = False
        self.logged_in = False
        self._node_capture = False
        self._node_in_table = False
        self._node_page_stop()
        self._pager_close()
        self._macro_captcha_active = False
        if self._login is not None:
            self._login.dispose()
        self.timers.stop_all()
        self.macros.stop()
        self.app.bus.publish("net.disconnected", account=self.account_id, reason=reason)
        if self.auto_reconnect and not self._manual_close and not self.app.shutting_down:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        # A4 定稿：3s → 6s → 12s → 60s 封顶
        delays = [3, 6, 12, 60]
        d = delays[min(self._reconnect_attempt, len(delays) - 1)]
        self._reconnect_attempt += 1
        self.app.bus.publish("net.connecting", account=self.account_id,
                             status=f"重连中({d}s)", attempt=self._reconnect_attempt)

        def go():
            if self._manual_close or not self.auto_reconnect:
                return
            args = dict(self._connect_args)
            if args:
                self.connect_to(**args)

        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(go)
        self._reconnect_timer.start(d * 1000)

    def cancel_reconnect(self) -> None:
        self.auto_reconnect = False
        if self._reconnect_timer is not None:
            self._reconnect_timer.stop()
            self._reconnect_timer = None

    def send(self, text: str) -> None:
        self._last_sent = text
        self._pager_close()  # 用户新发命令：通用分页会话到此结束
        # B3b 宏录制：录制用户从命令框/快捷动作/移动控制发出的手动命令
        if self._macro_recording and self._macro_recorder is not None:
            self._macro_recorder(text)
        # 宏等待输入：键入文本写入变量并继续（不发送游戏命令）
        if self.macros._waiting is not None:
            self.macros.resume_input(text)
            return
        if self.dsl.is_command(text):
            hit, result = self.dsl.evaluate(text)
            if hit and result is not None:
                self.app.bus.publish("ui.message", account=self.account_id, message=str(result))
            return
        self._track_pending(text)
        expanded = self.aliases.expand(text)
        if expanded:
            for cmd in expanded.split("\n"):
                for piece in cmd.split(";"):
                    piece = piece.strip()
                    if piece:
                        self.connection.send_line(piece)
        else:
            for cmd in text.split(";"):
                cmd = cmd.strip()
                if cmd:
                    self.connection.send_line(cmd)

    def _track_pending(self, text: str) -> None:
        """MapSync：记录用户发出的方向命令（含多个短名），供 GMCP.Move 确认对边。"""
        from xkxclient.core.map import _DIRS
        cmds = [c.strip() for c in text.replace("\n", ";").split(";") if c.strip()]
        cmd = ""
        for c in cmds:
            low = c.lower()
            if low in _DIRS:
                cmd = low
        if cmd:
            self.map_cache.set_pending_dir(cmd)

    def send_auto(self, text: str) -> None:
        """自动引擎（触发器/宏/战斗轮转）命令：走命令节流队列，避免冲击服务器缓冲。"""
        for cmd in text.split(";"):
            cmd = cmd.strip()
            if cmd:
                self.throttle.enqueue(cmd)

    # ---- 上行数据流 ----
    def _on_line(self, spans: list) -> None:
        text = "".join(s.text for s in spans)
        self.last_line = text
        self.app.bus.publish("net.text_display", account=self.account_id, line=text)
        if self._login and not self._login.finished:
            self._login.on_line(text)
        if self.logged_in:
            fired = self.triggers.handle_line(text)
            if not self._consume_line(text):
                self._route_line(text, spans, bool(fired))
        else:
            self.line_displayed.emit(spans, False)
        # 旁路解析（不阻塞主输出）：缓冲区告警 / look 捕获 / fullme 链接
        self._maybe_buffer_warning(text)
        self._maybe_cap_look(text)
        self._maybe_fullme(text)

    def _consume_line(self, text: str) -> bool:
        """统一消费层：判断本行是否被客户端静默消费（不进主输出）。

        数据流模型：服务器返回是一批连续数据，命令边界才可能穿插其他消息。
        因此每个「静默消费器」都以命令触发开启、以该命令的特征收尾（表尾框线/页尾
        提示）关闭，且只在该命令的捕获窗口内吞行，窗口外一行都不吞、一律照常上屏。

        优先级：翻页提示 > 裸 `>` 提示回显 > node 表格 > skills 表格。命中
        任意一项即返回 True，未命中返回 False，由调用方走正常路由上屏。
        """
        if self._is_pager(text):
            self._on_pager_line()
            return True
        if self._PROMPT_ECHO_RE.match(text):
            # 裸 `>` 提示行：回显噪声，静默吞掉；分页会话在此回到正常命令环
            self._pager_close()
            return True
        if self._consume_node_line(text):
            return True
        if self._consume_skills_line(text):
            return True
        if self._pager_pending:
            # 通用分页会话进行中：任何普通内容都算分页活动，刷新看门狗基线
            self._pager_last = time.monotonic()
            self._pager_timer.start()
        return False

    def _on_pager_line(self) -> None:
        """命中页尾提示行进处理。

        node 表格在捕获中且等待表格数据时，分页提示行晚于表格内容 ~1s 到达，
        属于表格活动：刷新看门狗基线后发空指令翻页。若看门狗/上一提示行已为
        本章节补发过（_node_page_sent=True），迟到的提示行只吞不发，避免重复
        空指令跳页；但保留看门狗继续运行——补发万一被服务器吞掉、下一页内容
        始终不来时，看门狗会再次补发。
        """
        if self._node_capture and self._node_in_table:
            self._node_page_touch()
            if self._node_page_sent:
                # 看门狗/提示行已补发过本章节，迟到的提示行只吞不发
                return
            self._pager_continue()
            self._node_page_sent = True        # 已为本章节发过自动翻页
            self._node_page_lines = 0          # 正常章节已翻页，行数从零累计下一页
            self._node_stray = 0
            return
        self._pager_arm()
        self._pager_continue()

        # ---- node 命令捕获：表格行拦截，不上主输出 ----
    def request_node(self) -> None:
        """发送 `node` 并开启捕获（dock 调用）。"""
        if not self.logged_in:
            return
        self._node_capture = True
        self._node_in_table = False
        self._node_capture_start = time.time()
        self._node_page_stop()
        self.connection.send_line("node")

    def _consume_node_line(self, text: str) -> bool:
        """捕获期间抑制 node 表格行，返回 True 表示本行不上主输出。

        状态机（按真实 node 表格格式）：
        - 表头/顶框线确认进入表格：含 `│` 且 `名称/目的地`，或以 `┌` 开头含 `─`；
          数据行兜底：表头页丢失时，首列 `[★☆]?ASCII名称│` 也能进表
        - 表格内：仅吞含竖线 `│` 的数据行、框线连接符（├┤┼┬┴）的行、表尾 `└…─…┘`
        - 表尾框线到达即结束捕获；空路径提示行到达也结束（照常上屏）
        - 未进表的普通文本不吞也不关闭捕获（避免漏掉后续表格）；
        - 进表后绝不因超时关闭：分页间隙（手动翻页等待/服务器慢）长短不影响捕获，
          只有表尾或空路径才结束。等待表头阶段保留短超时兜底。
        """
        if not self._node_capture:
            return False
        has_vbar = "│" in text or "|" in text
        frame = any(ch in text for ch in "├┤┼┬┴")
        if not self._node_in_table and time.time() - self._node_capture_start > 5.0:
            # 等表头超过 5s：直接关闭，不再等（进表后此分支不再生效）
            self._node_capture = False
            self._node_in_table = False
            return False
        if _NODE_EMPTY_MSG in text:
            # 空路径：捕获到此结束并静默（不上主输出）
            self._node_page_stop()
            self._node_capture = False
            self._node_in_table = False
            return True
        has_vbar = "│" in text or "|" in text
        frame = any(ch in text for ch in "├┤┼┬┴")
        if not self._node_in_table:
            # 确认进入表格：含竖线的表头（名称/目的地）或顶框线 `┌…─` 行。
            # 兜底：表头页丢失（分页交互/首段被吞）时，数据行本身也能进表
            # —— 首列 `[★☆]?ASCII名称` 后跟竖线即判定为 node 数据行。
            if (has_vbar and "名称" in text and "目的地" in text) or \
               (text.startswith("┌") and "─" in text) or \
               (_NODE_ROW_RE.match(text) is not None):
                self._node_in_table = True
                self._node_page_lines = 1   # 进表首行计入服务器分的页行数
                self._node_page_touch()     # 进表即启动持续看门狗
                return True
            return False  # 未进表：不吞，继续等表头（超时兜底）
# 已在表格内：仅吞表格结构行
        if "└" in text and "─" in text:
            # 表尾框线行：吞掉并结束捕获，重置分页状态
            self._node_page_stop()
            self._node_capture = False
            self._node_in_table = False
            return True
        if has_vbar or frame:
            # 吞入表格结构行并累计行数
            self._node_page_lines += 1
            self._node_stray = 0
            if self._node_page_sent:
                # 自动翻页已发过、现在又收到表格行：翻页生效，重新累计下一页
                # 行数并复位重试计数（支持多页连续翻）
                self._node_page_lines = 1
                self._node_page_sent = False
                self._node_page_retries = 0
            # 有表格活动：刷新停摆基线，重置看门狗窗口
            self._node_page_touch()
            return True   # 数据行 / 水平分隔线
        # 表格内普通文本：仅在自动翻页后按杂行计数，连续 _NODE_STRAY_MAX
        # 行无表格内容 ⇒ 判定 node 已无数据，关闭捕获；否则照常上屏
        if self._node_page_sent:
            self._node_stray += 1
            if self._node_stray >= _NODE_STRAY_MAX:
                self._node_page_stop()
                self._node_capture = False
                self._node_in_table = False
        return False      # 普通文本照常上屏

    def _node_page_touch(self) -> None:
        """表格/提示行活动到达：刷新停摆基线并重置看门狗窗口。

        看门狗语义：停摆 = 自最后一次活动起 _NODE_PAGE_GAP 秒无表格内容。
        每次活动都重新计时，因此持续的表格流永远不会触发翻页；只有表格
        真正停摆（服务器在等待分页输入）才会补发。"""
        self._node_page_last_active = time.monotonic()
        self._node_page_timer.start()

    def _node_page_stop(self) -> None:
        """重置 node 分页看门狗状态（表尾/关闭/断线时统一调用）。"""
        self._node_page_timer.stop()
        self._node_page_lines = 0
        self._node_page_sent = False
        self._node_stray = 0
        self._node_page_last_active = 0.0
        self._node_page_retries = 0

    def _node_page_fallback(self) -> None:
        """持续看门狗（_NODE_PAGE_GAP）。表格在停摆 GAP 秒后仍无新内容且
        未到表尾 → 判定服务器分页停在等待输入（提示行缺失/未识别/补发被
        吞），静默补发空指令翻页。补发后仍无进展则按 _NODE_PAGE_RETRY_MAX
        重试，超过上限静默放弃，交给 navdock 捕获超时收尾。
        """
        if not (self._node_capture and self._node_in_table):
            return
        now = time.monotonic()
        if now - self._node_page_last_active < _NODE_PAGE_GAP:
            # 已有活动刷新过基线：本次触发无效，重新计时继续观察
            self._node_page_timer.start()
            return
        if self._node_page_sent and self._node_page_retries >= _NODE_PAGE_RETRY_MAX:
            # 已多次补发仍无进展：停止探测，等 navdock 捕获超时收尾
            return
        self._node_page_sent = True
        self._node_page_retries += 1
        self._node_page_last_active = now
        self._pager_continue()
        self._node_page_timer.start()

    def abort_node_capture(self) -> None:
        """外部（navdock 捕获超时）强制中止 node 捕获：停止吞行与看门狗，
        避免服务器分页卡死时主输出被长期抑制。"""
        if not self._node_capture:
            return
        self._node_capture = False
        self._node_in_table = False
        self._node_page_stop()

    def _route_line(self, text: str, spans: list, highlight: bool = False) -> None:
        """频道分流（B5e）：【频道】行进聊天栏（富文本），永不回主输出；说道等对话直接进主屏。"""
        channel = self.channel_of(text)
        if channel:
            on = self._channels.get(channel, True)
            if not on:
                return  # 阻断频道：聊天栏与主输出都不显示
            self.channel_text.emit(channel, spans, highlight)
            return  # 只进聊天栏，不再进主输出
        self.line_displayed.emit(spans, highlight)

    def channel_of(self, text: str) -> str | None:
        if text.startswith("【") and "】" in text and text[1:2] != "】":
            name = text[1:text.index("】")]
            if name.strip() and name not in self._channels:
                self._channels[name] = True
                cfg = ConfigManager.instance()
                ch = dict(cfg.get("channels") or {})
                ch[name] = True
                cfg.set("channels", ch)
            return name.strip() if name.strip() else None
        # 房间内对话（`张三说道：「...」`）不再分流：直接进主屏
        return None

    def _maybe_buffer_warning(self, text: str) -> None:
        if any(k in text for k in _BUF_PROMPTS):
            self.throttle.on_buffer_warning()

    # ---- 分页翻页 ----
    # 识别页尾提示：放宽锚定覆盖常见分页提示变体——
    #   `== 未完继续 40% == (...)` / `==未完继续==` / `未完 N%` / `未完（回车…`
    #   `回车继续` / `继续下一页` / `q 离开`（“未完”后非「继续/%/括号」不命中，
    #   如“故事未完待续”不上钩；普通对话文本几乎不含这些组合）。
    _PAGER_RE = re.compile(
        r"未完(?:继续|(?:\s*\d*(?:\.\d+)?\s*%|\s*[（(]))"
        r"|回车继续|继续下一页|q\s*离开"
    )
    # 裸 `>` 提示行（MudOS 命令提示/分页结束后回提示的回显）。分页自动翻页与
    # 各类静默命令结束后都会回显这类行，属提示噪声，静默吞掉保持界面整洁。
    _PROMPT_ECHO_RE = re.compile(r"^\s*>\s*$")
    _PAGER_AUTO = True
    _PAGER_LAST_SEND = 0.0

    def _is_pager(self, text: str) -> bool:
        """页尾提示识别：行内含「未完继续 + %」即认为分页，自动发空行继续。"""
        if not self.logged_in or not self._PAGER_AUTO:
            return False
        return self._PAGER_RE.search(text) is not None

    def _pager_continue(self) -> None:
        """命中翻页提示：发空命令（回车）继续下一页。限频防刷屏。

        B5-3 设计即「自动发送空命令继续翻页」（回车=空命令=「其他继续下一页」），
        node 分页器实测同样接受回车空命令；空命令对各类分页器兼容性最好。
        注意：不在此处重置 node 分页状态——`_node_page_sent` 由
        `_on_pager_line` / 看门狗 `_node_page_fallback` 置位，迟到提示
        行到来时在 `_on_pager_line` 消费（防重复翻页）。
        """
        now = time.time()
        if now - self._PAGER_LAST_SEND < 0.12:
            return
        self._PAGER_LAST_SEND = now
        self.connection.send_line("")
        # 分页间隙不打断进行中的 node/look 捕获（换页未到表尾，续命等待后续表格行）
        if self._node_capture and self._node_in_table:
            self._node_capture_start = now

    def _pager_arm(self) -> None:
        """识别到页尾提示行：进入（或续命）通用分页会话并启动看门狗。"""
        self._pager_pending = True
        self._pager_retries = 0
        self._pager_last = time.monotonic()
        self._pager_timer.start()

    def _pager_close(self) -> None:
        """分页会话结束（回到裸 `>` 提示 / 用户新发命令 / 重试封顶）。"""
        self._pager_pending = False
        self._pager_retries = 0
        self._pager_timer.stop()

    def _pager_fallback(self) -> None:
        """通用分页看门狗（_PAGER_GAP）。分页会话内容停摆 GAP 秒仍无进展 →
        判定服务器分页在等输入（提示行缺失/未识别/翻页键未被接受），补发空格；
        补发仍无进展则按 _PAGER_RETRY_MAX 重试，超上限静默放弃。
        node 捕获期间不干预——node 有自己的分页看门狗。
        """
        if not self._pager_pending:
            return
        if self._node_capture:
            self._pager_last = time.monotonic()
            self._pager_timer.start()
            return
        now = time.monotonic()
        if now - self._pager_last < _PAGER_GAP:
            # 已有活动刷新过基线：继续观察
            self._pager_timer.start()
            return
        if self._pager_retries >= _PAGER_RETRY_MAX:
            self._pager_close()
            return
        self._pager_retries += 1
        self._pager_last = now
        self._pager_continue()
        self._pager_timer.start()

    def _maybe_fullme(self, text: str) -> None:
        # 宏验证码步骤期间：fullme 链接由宏引擎消费，避免再弹普通 fullme 窗
        if getattr(self, "_macro_captcha_active", False):
            return
        url = extract_fullme_url(text)
        if url:
            if self._fullme_collect > 0:
                # 「开 4 窗」模式：服务器只给 1 个链接，同链接可开 4 次。
                # 收到第一个有效链接即打开网格窗口；字符仍继续。
                self._fullme_collect = 0
                self.app.bus.publish("fullme.grid", account=self.account_id, urls=[url])
            else:
                src = "manual" if (self._last_sent or "").strip().startswith("fullme") else "task"
                self.app.bus.publish("fullme.detected", account=self.account_id,
                                     source=src, url=url)
        elif "fullme" in text and "验证码" not in text:
            return

    def request_full_4(self) -> None:
        """手动开 2×2 fullme 网格：服务器只给 1 个 fullme 链接（可开 4 次），
        收到该链接后在同一网格窗口 4 格各加载一次。"""
        if self._fullme_collect > 0:
            return
        self._fullme_collect = 1
        self._fullme_urls = []
        self.connection.send_line("fullme")

    def _maybe_cap_look(self, text: str) -> None:
        if not self._capture_look:
            return
        if "MXP" in text:
            return
        self._look_buf += text + "\n"
        if ("出口有" in text or "出口是" in text or "方向有" in text or "方向是" in text) \
           or ("这里是" in text and len(self._look_buf) > 40) \
           or (not text and len(self._look_buf) > 20):
            self._capture_look = False
            buf = self._look_buf
            self._look_buf = ""
            res = self.look_parser.handle_line(buf, account=self.account_id)
            self._apply_look_room(res)

    def _apply_look_room(self, res) -> None:
        """look 解析出的出口写回 session，并发布 state.room 供移动 dock 启用方向按钮。

        GMCP.Move 仅在移动后才推送，登录后房间出口只能靠 look 获取。
        """
        if res is None or not getattr(res, "room", None):
            return
        room = res.room
        if getattr(room, "name", ""):
            self.room_name = room.name
        if getattr(room, "exits", None):
            self.exits = list(room.exits)
            self.app.bus.publish("state.room", account=self.account_id,
                                 name=self.room_name, exits=self.exits)

    def _consume_skills_line(self, text: str) -> bool:
        """技能表格捕获（进面板），不吞屏：skills 表照常上主输出，面板仅旁路解析。

        以 `send_skills` 开启，以技能槽摘要行 / 表尾框线 `└` / 8s 超时结束。
        只累积「像技能表格」的行（框线/分隔符/技能行/技能槽行），
        人物名、门派、等级等普通文本即使混入也不会污染技能面板。
        """
        if not self._capture_skills:
            return False
        if not self._is_skill_table_line(text):
            return False
        self._skills_buf += text + "\n"
        if ("技能槽" in text or "空余" in text) or \
           (self._skills_started and time.time() - self._skills_started > 8.0) or \
           ("└" in text or "┘" in text):
            self._capture_skills = False
            if self.skills_dock is not None:
                self.skills_dock.on_skills(self._skills_buf)
            self._skills_buf = ""
        return False  # 技能表照常上屏，仅静默喂给面板

    _SKILL_TABLE_HINT = ("│", "丨", "|", "┌", "┐", "├", "┤", "└", "┘", "─")

    def _is_skill_table_line(self, text: str) -> bool:
        """技能输出大多数行带框线/分隔符；表头/分组行也用框线，技能槽摘要行含「技能槽/空余」。
        只有这类行才进入技能捕获缓冲，其他文本（人物名/门派/等级等）一律丢弃。"""
        if "技能槽" in text or "空余" in text:
            return True
        return any(ch in text for ch in self._SKILL_TABLE_HINT)

    def send_skills(self) -> None:
        self._capture_skills = True
        self._skills_started = time.time()
        self._skills_buf = ""
        self.connection.send_line("skills")

    def _send_look(self) -> None:
        self.connection.send_line("look")
        self._capture_look = True
        self._look_buf = ""

    def cn_name(self) -> str:
        """玩家中文名：优先账号配置持久化的 cn_name（登录时 GMCP.Status 首条真实身份），
        其次 state.name（id 不含 `#` 时才存到），保证不被战斗敌名污染。"""
        try:
            accs = ConfigManager.instance().accounts()
            data = accs.get(self.account_id)
            if isinstance(data, dict):
                cn = data.get("cn_name")
                if cn:
                    return str(cn)
        except Exception:
            pass
        name = getattr(self.state, "name", "")
        return name or ""

    def _backfill_account(self) -> None:
        """D1 登录后信息回填：GMCP Status 的 中文名/门派/级别/头衔 写回账号持久化。

        仅登录完成后、字段有新值时写回，避免覆盖手动填写的用户名。
        """
        if not self.logged_in:
            return
        st = self.state
        if not getattr(st, "name", ""):
            return
        cfg = ConfigManager.instance()
        accs = cfg.accounts()
        data = accs.get(self.account_id)
        if not isinstance(data, dict):
            return
        from xkxclient.core.crypto import encrypt_password

        changed = False
        info = {
            "cn_name": st.name,
            "title": st.title,
            "family": st.family,
            "level": st.level,
        }
        for key, val in info.items():
            if val not in (None, "", 0) and data.get(key) != val:
                data[key] = val
                changed = True
        if changed:
            cfg.save_account(self.account_id, data)

    def _on_gmcp(self, payload: bytes) -> None:
        module, data = gmcp.parse_payload(payload)
        if module.startswith("GMCP."):
            module = module[len("GMCP."):]
        module = module.split(".", 1)[0]
        if not module.startswith("GMCP."):
            module = "GMCP." + module
        self.app.bus.publish(module, account=self.account_id, data=data)
        if ConfigManager.instance().get("debug.gmcp_log", False):
            self._gmcp_log(module, data)
        if module == "GMCP.Status" and isinstance(data, dict):
            busy = data.get("is_busy", getattr(self.state, "busy", None))
            fighting = data.get("is_fighting", getattr(self.state, "fighting", None))
            if busy is not None:
                self.state.busy = busy
            if fighting is not None:
                self.state.fighting = fighting
            if self.state.update_from_gmcp_status(data):
                self.app.bus.publish("state.changed", account=self.account_id, state=self.state)
                self._backfill_account()
        elif module == "GMCP.Move":
            if isinstance(data, list):
                d = data[0] if data and isinstance(data[0], dict) else {}
            elif isinstance(data, dict):
                d = data
            else:
                d = {}
            ok = d.get("result")
            if not isinstance(ok, bool):
                ok = str(ok or "").strip().lower() in ("true", "1")
            self.navigator.on_move(ok)
            if ok:
                self.room_name = str(d.get("short") or self.room_name)
                self.exits = list(d.get("dir") or [])
                self.map_cache.on_move(d)
                self.app.bus.publish("GMCP.Move", account=self.account_id, data=d)
                self.app.bus.publish("state.room", account=self.account_id, name=self.room_name, exits=self.exits)
                if self.auto_look:
                    self._send_look()
        elif module == "GMCP.Combat":
            if isinstance(data, list):
                d = None
                for it in data:
                    if isinstance(it, dict):
                        d = it
                        break
                data = d or {}
            if not isinstance(data, dict):
                data = {}
            self.state.update_enemy(data)
            pfm = data.get("perform_id") or data.get("perform")
            if pfm:
                self.state.update_perform_cd(pfm, data)
            self.app.bus.publish("GMCP.Combat", account=self.account_id, data=data)
            self.app.bus.publish("state.combat", account=self.account_id, enemy=self.state.enemy)
            if data.get("enemy_out"):
                self.app.bus.publish("state.changed", account=self.account_id, state=self.state)
        elif module == "GMCP.Buff":
            if isinstance(data, (dict, list)):
                if self.state.update_buffs(data):
                    self.app.bus.publish("state.buffs", account=self.account_id, buffs=self.state.buffs)
            self.app.bus.publish("GMCP.Buff", account=self.account_id, data=data)
        elif module == "GMCP.Message" and isinstance(data, list):
            for item in data if isinstance(data, list) else [data]:
                if isinstance(item, dict):
                    self.app.bus.publish("GMCP.Message", account=self.account_id, data=item,
                                         name=str(item.get("name", "")), url=str(item.get("url", "")))
    def _gmcp_log(self, module: str, data) -> None:
        try:
            cfg = ConfigManager.instance()
            line = f"{module}: {json.dumps(data, ensure_ascii=False)}\n"
            with open(cfg.root / "gmcp_recv.log", "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def set_channel(self, name: str, on: bool) -> None:
        self._channels[name] = on
        cfg = ConfigManager.instance()
        ch = dict(cfg.get("channels") or {})
        ch[name] = on
        cfg.set("channels", ch)

    def close(self) -> None:
        self._manual_close = True
        self.map_cache.flush()
        if self._reconnect_timer is not None:
            self._reconnect_timer.stop()
            self._reconnect_timer = None
        if self._login is not None:
            self._login.dispose()
        self.timers.stop_all()
        self.macros.stop()
        self.throttle.cancel_all()
        self.throttle.close()
        self.combat.close()
        self.connection.close()

    def logout(self) -> None:
        """客户端关闭前的优雅登出：发送 quit 让服务器存档/清理，避免直接断线丢物品。

        仅对已登录且有连接时发送；不阻塞、不等服务器应答（等待由 app.shutdown 统一处理）。
        """
        if not self.logged_in or not self.connected:
            return
        try:
            self.connection.send_line("quit")
        except Exception:
            pass


class LoginMachine:
    """D2 完整自动登录：等提示语发送，不盲目发送。

    登录完成判定：收到服务器回退到普通文本模式的消息
    「你的客户端不支持MXP功能，使用普通文本模式」才算完成。
    本客户端不实现 MXP，因此不回 <SUPPORT>；若 5s 后仍未收到该消息，
    发一次空指令（回车）强制回退。25s 看门狗仅作应急兜底。
    """

    def __init__(self, session, username, password, init_cmds, autologin,
                 register: bool = False) -> None:
        self.session = session
        self.username = username
        self.password = password
        self.init_cmds = init_cmds
        self.autologin = autologin
        self.register = register
        self.finished = False
        # 编码提示(Input 1 for GBK...)无需在名字阶段应答：服务器会随后自动出现
        # 英文名字提示。连接很快补发一次编码数字即可让服务器继续（替代旧版即时 Core.Hello）。
        self.stage = "name" if autologin else None
        self.started = time.time()
        self.sent_name = False
        self.sent_password = False
        self._enc_sent = False
        self._pushed_empty = False
        self._answered_kick = False
        self._mudlet_sends = 0
        self._mudlet_timer: QTimer | None = None
        self._send_timer: QTimer | None = None
        self._push_timer: QTimer | None = None
        self._watch_timer: QTimer | None = None
        self._skip_timer: QTimer | None = None

        # 看门狗：密码已发后 25s 仍未收到 MXP 回退消息则应急判定完成（防死等）。
        # 正常路径：服务器 ~5s 发「不支持MXP...普通文本模式」或空指令触发后完成。
        self._watch_timer = QTimer(session)
        self._watch_timer.setSingleShot(True)
        self._watch_timer.timeout.connect(self._timeout_finish)
        self._watch_timer.start(25000)

        # 连接后补发编码数字（发电指令时服务器才继续到名字提示）。
        # 依据连接编码选择：GBK→1，UTF8→2，BIG5→3；缺省 GBK→1。
        if autologin and not register:
            enc = (session.connection.encoding or "").lower()
            num = "2" if enc.startswith("utf") else "3" if enc == "big5" else "1"
            self._enc_sent = True
            self._schedule_send(num, 450)

    def _encoding_line(self) -> str:
        """若服务器主动提示「Input 1 for GBK...」等编码选择，还没发过才补发。"""
        if not self._enc_sent:
            enc = (self.session.connection.encoding or "").lower()
            self._enc_sent = True
            return "2" if enc.startswith("utf") else "3" if enc == "big5" else "1"
        return ""

    # ---- 计划发送 ----
    def _schedule_send(self, text: str, after_ms: int = 500) -> None:
        def go():
            if self.finished:
                return
            self.session.connection.send_line(text)
        self._send_timer = QTimer(self.session)
        self._send_timer.setSingleShot(True)
        self._send_timer.timeout.connect(go)
        self._send_timer.start(after_ms)

    def on_line(self, text: str) -> None:
        if self.finished:
            return
        _dbg = getattr(self.session, "_login_debug", False)
        if _dbg:
            print(f"[login] stage={self.stage} sent_name={self.sent_name} sent_pw={self.sent_password} text={text!r}")
        if self.stage in ("name", "name_sent"):
            if not self.sent_name and not self._enc_sent and any(k in text for k in DEFAULT_PROMPTS["encoding"]):
                self.session.connection.send_line(self._encoding_line())
            if not self.sent_name and self.register and "要注册新人物" in text:
                # 注册模式：被提示输入 new 时，发 new 并停掉登录机，交给用户
                self.sent_name = True
                self.stage = "name_sent"
                self.session.connection.send_line("new")
                self.finished = True
                return
            if _REAL_NAME_RE.search(text) and not self.sent_name:
                if self.username:
                    self.sent_name = True
                    self.stage = "name_sent"
                    self.started = time.time()
                    self._schedule_send(self.username, 500)
                else:
                    if self.register:
                        self.sent_name = True
                        self.stage = "name_sent"
                        self.session.connection.send_line("new")
                        self.finished = True
                    else:
                        self.finished = True
                return
        if self.stage == "name_sent":
            if self._is_password_prompt(text) and not self.sent_password:
                self._send_password()
                return
        # 登录完成条件：服务器回退到普通文本模式的消息
        # 「你的客户端不支持MXP功能，使用普通文本模式」
        if self.stage in ("password", "password_sent"):
            if "不支持MXP" in text or "使用普通文本模式" in text:
                self._finish()
                return
            # 服务器开始探测 MXP：立刻回车进入普通模式，避免一直卡在握手
            if "即将开始检测你的客户端" in text and not self._pushed_empty:
                self._pushed_empty = True
                self._schedule_send("", 200)
            # 收到「推荐使用Mudlet」：MXP 推荐/回车屏，间隔0.5s发空指令快速越过
            if "推荐使用Mudlet" in text and self._mudlet_timer is None:
                self._start_mudlet_spam()
            # 重复登录：服务器询问「是否踢掉相同名字的在线角色」-> 答 y 接管
            if ("y/n" in text or "杀掉" in text or "杀出去" in text) and not self._answered_kick:
                self._answered_kick = True
                self._schedule_send("y", 500)

    def _timeout_finish(self) -> None:
        if self.finished:
            return
        # 应急兜底（正常在 MXP 回退消息后 ~5s 内完成）
        if self.sent_password:
            self._finish()

    def _is_password_prompt(self, text: str) -> bool:
        return any(k in text for k in DEFAULT_PROMPTS["password"])

    def _arm_mxp_push(self) -> None:
        """密码已发、未收到回退消息时：5s 后发一次空指令（回车）强制回退普通文本模式。"""

        def push():
            if self.finished or self.sent_password is False:
                return
            if not self._pushed_empty:
                self._pushed_empty = True
                self.session.connection.send_line("")

        self._push_timer = QTimer(self.session)
        self._push_timer.setSingleShot(True)
        self._push_timer.timeout.connect(push)
        self._push_timer.start(5000)

    def _start_mudlet_spam(self) -> None:
        """收到「推荐使用Mudlet」：间隔0.5s发一次空指令，越过 MXP 回车屏直到登录完成。"""

        def spam():
            if self.finished:
                return
            if self._mudlet_sends >= 20:
                self._stop_mudlet_spam()
                return
            self._mudlet_sends += 1
            self.session.connection.send_line("")

        self._mudlet_timer = QTimer(self.session)
        self._mudlet_timer.setInterval(500)
        self._mudlet_timer.timeout.connect(spam)
        self._mudlet_timer.start()

    def _stop_mudlet_spam(self) -> None:
        if self._mudlet_timer is not None:
            self._mudlet_timer.stop()
            self._mudlet_timer = None

    def _send_password(self) -> None:
        if self.password:
            self.sent_password = True
            self.stage = "password_sent"
            self.started = time.time()
            self._schedule_send(self.password, 500)
            self._arm_mxp_push()
        else:
            self.finished = True

    def _finish(self) -> None:
        self.finished = True
        self._stop_mudlet_spam()
        self.session.logged_in = True
        self.session.state.id = self.session.account_id
        # C1：登录完成后才补发 GMCP Core.Hello（连接早期发会被当作名字输入）
        self.session.connection.start_gmcp_hello()
        for cmd in self.init_cmds:
            if cmd.strip():
                self.session.connection.send_line(cmd)
        self.session.app.bus.publish("login.done", account=self.session.account_id)
        # C1：登录后订阅 GMCP 频道（tune gmcp <Name> on）
        for ch in _GMCP_SUBSCRIBE:
            self._schedule_send(f"tune gmcp {ch} on", 2500)
        # 登录后自动 look 一次，获取房间出口（GMCP.Move 仅在移动后推送，
        # 刚登录时方向按钮靠 look 解析的出口启用）
        self.session._capture_look = True
        self.session._look_buf = ""
        self._schedule_send("look", 3200)

    def dispose(self) -> None:
        """断开/关闭时调用：取消挂起的定时器，避免退出时 QTimer 竞争崩溃。"""
        self.finished = True
        for t in (self._send_timer, self._skip_timer, self._watch_timer, self._push_timer, self._mudlet_timer):
            if t is not None:
                t.stop()
        self._send_timer = None