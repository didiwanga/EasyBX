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
from xkxclient.core.state import CharacterState
from xkxclient.net.connection import Connection
from xkxclient.parse.look import LookParser

_GMCP_SUBSCRIBE = ["Status", "Move", "System", "Combat", "Buff"]

# 「命令进入缓冲」提示（服务端命令缓冲限流，见 wiki about_cmdbuffer）
_BUF_PROMPTS = ("命令进入缓冲", "命令缓冲", "指令进入缓冲")

# node 表格框线字符：捕获期间仅抑制含这些字符的行，其余信息照常上屏
_NODE_BOX_CHARS = "│┌┐└┘├┤─"
_NODE_EMPTY_MSG = "这里没有玩家定义的路径"

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
            if self._is_pager(text):
                # 翻页提示行：发空指令继续下一页，本行不上屏（含聊天栏/主输出）
                self._pager_continue()
            elif self._consume_node_line(text):
                # node 表格行：不上主输出（已由 dock 订阅 net.text_display 解析）
                pass
            else:
                self._route_line(text, spans, bool(fired))
        else:
            self.line_displayed.emit(spans, False)
        self._maybe_buffer_warning(text)
        self._maybe_cap_skills(text)
        self._maybe_cap_look(text)
        self._maybe_fullme(text)

        # ---- node 命令捕获：表格行拦截，不上主输出 ----
    def request_node(self) -> None:
        """发送 `node` 并开启捕获（dock 调用）。"""
        if not self.logged_in:
            return
        self._node_capture = True
        self._node_in_table = False
        self._node_capture_start = time.time()
        self.connection.send_line("node")

    def _consume_node_line(self, text: str) -> bool:
        """捕获期间抑制 node 表格行，返回 True 表示本行不上主输出。

        状态机：只有确认已进入表格（表头 `名称/目的地` 或顶框线 `┌`）才开始吞行；
        未进表格的普通文本（闲聊、`这里没有玩家定义的路径` 提示等）一律照常上屏。
        - 表格内：仅吞框线字符行；表格内夹杂的无框线文本也照常上屏
        - 页尾框线 `└…┘` 到达即结束捕获；空路径提示行到达也结束捕获（照常上屏）
        - 5s 超时兜底：异常中断时自动关闭捕获，避免长期拦截
        """
        if not self._node_capture:
            return False
        if time.time() - self._node_capture_start > 5.0:
            self._node_capture = False
            self._node_in_table = False
            return False
        if _NODE_EMPTY_MSG in text:
            # 空路径：非表格信息，照常上屏，但捕获到此结束
            self._node_capture = False
            self._node_in_table = False
            return False
        has_box = any(ch in text for ch in _NODE_BOX_CHARS)
        if not self._node_in_table:
            # 确认进入表格：表头（名称/目的地）或顶框线 `┌` 行
            if ("名称" in text and "目的地" in text) or ("┌" in text and "─" in text):
                self._node_in_table = True
                return True
            return False  # 未进表格：不拦截，照常上屏
        # 已在表格内：仅吞框线行
        if not has_box:
            return False
        if "└" in text:
            # 表尾框线：本行也是表格内容，吞掉并结束捕获
            self._node_capture = False
            self._node_in_table = False
        return True

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
    # 精确匹配整行：`== 未完继续 40% == (q 离开，b 前一页，其他继续下一页)`
    # 只识别完整页尾提示，避免误过滤普通文本。数字可带小数（如 34.5%）。
    _PAGER_RE = re.compile(
        r"^\s*==\s*未完继续\s*\d+(?:\.\d+)?\s*%\s*==\s*"
        r"\(q\s*离开[，,]?\s*b\s*前一页[，,]?\s*其他继续下一页\)\s*$"
    )
    _PAGER_AUTO = True
    _PAGER_LAST_SEND = 0.0

    def _is_pager(self, text: str) -> bool:
        """页尾提示识别：完整匹配「== 未完继续 N% == (q 离开，b 前一页，其他继续下一页)」。"""
        if not self.logged_in or not self._PAGER_AUTO:
            return False
        return self._PAGER_RE.match(text) is not None

    def _pager_continue(self) -> None:
        """命中翻页提示：发空指令（回车）继续下一页。限频防刷屏。"""
        now = time.time()
        if now - self._PAGER_LAST_SEND < 0.12:
            return
        self._PAGER_LAST_SEND = now
        self.connection.send_line("")

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
        if ("出口是" in text) or ("这里是" in text and len(self._look_buf) > 40) or (not text and len(self._look_buf) > 20):
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

    def _maybe_cap_skills(self, text: str) -> None:
        if self._capture_skills:
            self._skills_buf += text + "\n"
            if "技能槽" in text or "空余" in text:
                self._capture_skills = False
                if self.skills_dock is not None:
                    self.skills_dock.on_skills(self._skills_buf)
                self._skills_buf = ""

    def send_skills(self) -> None:
        self._capture_skills = True
        self._skills_buf = ""
        self.connection.send_line("skills")

    def _send_look(self) -> None:
        self.connection.send_line("look")
        self._capture_look = True
        self._look_buf = ""

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