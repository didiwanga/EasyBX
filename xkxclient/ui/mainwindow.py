from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QResizeEvent
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDockWidget, QLabel, QMainWindow,
                             QMessageBox, QProgressBar, QSizePolicy, QTabWidget, QToolBar,
                             QToolButton, QVBoxLayout, QWidget)

from xkxclient.core import config as cfg
from xkxclient.core import resources
from xkxclient.core.shortcuts import ShortcutManager
from xkxclient.core.updater import UpdateManager, frozen
from xkxclient.ui import editors
from xkxclient.ui.about import AboutDialog
from xkxclient.ui.automationdock import (MacroControlDock, MacroRecorderDock,
                                         MoveControlDock, QuickActionsDock)
from xkxclient.ui.combatdock import CombatAssistDock
from xkxclient.ui.commands import CommandPanel, CommandStore
from xkxclient.ui.dslmanual import DslManualPanel
from xkxclient.ui.lookdock import LookDock
from xkxclient.ui.mapdock import MapDock
from xkxclient.ui.navdock import NavDock
from xkxclient.ui.notepaddock import NotepadDock
from xkxclient.ui.settings import CloseBehaviorDialog, EnvSettingsDialog, ShortcutDialog
from xkxclient.ui.skillsdock import SkillsDock
from xkxclient.ui.statusdock import StateDock
from xkxclient.ui.statusbar import StatusBar
from xkxclient.ui.tabs import AccountTab
from xkxclient.ui.theme import PALETTES, apply as apply_theme
from xkxclient.ui.tray import AppTray
from xkxclient.ui.xiuxiandock import XiuxianDock


class ResizableDock(QDockWidget):
    """PyQt6 QDockWidget 浮动时内部 widget 不跟随窗口缩放，重写 resizeEvent 同步。

    停靠时 QDockWidget 由 QMainWindow 布局管理，尺寸由内容推导；浮动时作为独立
    窗口，需手动把内容 widget 铺满内部可用区域，否则拖拽边缘无法改变面板宽度。
    """

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        w = self.widget()
        if w is None:
            return
        if self.isFloating():
            # 浮动窗口：内容铺满内部可用区域，宽度任意（内部布局按可用宽度流式重排）
            r = self.contentsRect()
            if r.width() > 0 and r.height() > 0:
                w.resize(r.size())


class MainWindow(QMainWindow):
    """主窗口（E8-主窗口布局总纲）：菜单栏 + 工具栏 + QTabWidget + QStatusBar + Docks。"""

    def __init__(self, app, parent=None) -> None:
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("EasyBXb")
        if (icon := resources.app_icon()) is not None:
            self.setWindowIcon(icon)
        self.resize(1280, 820)

        self.status = StatusBar(self)
        self.setStatusBar(self.status)

        self.tabs = QTabWidget(self)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        # docks
        self.skills_dock = self._make_dock("技能面板", SkillsDock(None))
        self.state_dock = self._make_dock("状态", StateDock())
        self.commands_dock = self._make_dock("命令速查", CommandPanel(CommandStore()))
        self.dsl_dock = self._make_dock("DSL 手册", DslManualPanel())
        self.quick_dock = self._make_dock("快捷动作", QuickActionsDock(None))
        self.move_dock = self._make_dock("移动控制", MoveControlDock(None))
        self.macro_dock = self._make_dock("宏控制", MacroControlDock(None))
        self.recorder_dock = self._make_dock("宏录制", MacroRecorderDock(None))
        self.combat_dock = self._make_dock("自动战斗", CombatAssistDock(None))
        self.xiuxian_dock = self._make_dock("辅助修炼", XiuxianDock(None))
        self.map_dock = self._make_dock("地图", MapDock(None))
        self.nav_dock = self._make_dock("导航目的地", NavDock(None))
        self.look_dock = self._make_dock("房间详情", LookDock(None))
        self.notepad_dock = self._make_dock("记事本", NotepadDock(None))
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.skills_dock)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.state_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.commands_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dsl_dock)
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, self.quick_dock)
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, self.move_dock)
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, self.macro_dock)
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, self.recorder_dock)
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, self.combat_dock)
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, self.xiuxian_dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.map_dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.nav_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.look_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.notepad_dock)

        self._cur_tab: AccountTab | None = None
        self._docks_restored = False
        self._layout_healed = False
        self._layout_diag = False
        ShortcutManager.instance().attach(self)
        self._build_menus()
        self._build_toolbar()
        self._build_shortcuts()

        self.app.bus.subscribe("net.connecting", self._on_net_event)
        self.app.bus.subscribe("net.connected", self._on_net_event)
        self.app.bus.subscribe("net.disconnected", self._on_net_event)
        self.app.bus.subscribe("state.changed", self._on_state_changed)
        self.app.bus.subscribe("GMCP.Combat", self._on_combat)
        self.app.bus.subscribe("state.combat", self._on_enemy)
        self.app.bus.subscribe("state.buffs", self._on_buffs)
        self.app.bus.subscribe("state.room", self._on_room)
        self.app.bus.subscribe("login.done", self._on_login_done)
        self.app.bus.subscribe("ui.message", self._on_status_message)
        self.app.bus.subscribe("fullme.detected", self._on_fullme)
        self.app.bus.subscribe("fullme.grid", self._on_fullme_grid)
        self.app.bus.subscribe("hongbao.detected", self._on_hongbao)
        self.app.bus.subscribe("macro.start", self._on_macro_progress)
        self.app.bus.subscribe("macro.end", self._on_macro_progress)
        self.app.bus.subscribe("macro.wait_input", self._on_macro_wait)
        self.app.bus.subscribe("net.throttle", self._on_throttle)

        self.tray = AppTray(self)
        self.tray.show()

        self.commands_dock.widget().fill_requested.connect(self._command_fill)
        self.commands_dock.widget().send_requested.connect(self._command_send)

        self._restore_layout()

        # 自动更新：打包环境启动后异步检查服务器新版本
        if frozen():
            self._updater = UpdateManager(self.app, self)
            QTimer.singleShot(2000, self._updater.start)

    def _check_update_now(self) -> None:
        """菜单「检查更新」：立即（再）检查一次服务器新版本，并给出结果提示。"""
        if not hasattr(self, "_updater"):
            self._updater = UpdateManager(self.app, self)
        self._updater.start(manual=True)

    # ---- 装配工具 ----
    def _make_dock(self, title: str, widget: QWidget) -> QDockWidget:
        dock = ResizableDock(title, self)
        dock.setWidget(widget)
        dock.setObjectName(f"dock_{title}")
        return dock

    def _build_menus(self) -> None:
        bar = self.menuBar()
        fm = bar.addMenu("文件")
        fm.addAction("新建连接…", self._new_connection)
        fm.addAction("断开当前标签", self._disconnect_current)
        fm.addAction("重新连接", self._reconnect_current)
        fm.addSeparator()
        fm.addAction("关闭行为…", lambda: CloseBehaviorDialog(self).exec())
        fm.addSeparator()
        fm.addAction("退出", self.close)

        em = bar.addMenu("编辑")
        em.addAction("查找…", self._show_find)
        em.addAction("字体设置…", self._open_font)
        em.addAction("清屏", self._clean_screen)
        em.addAction("清空历史", self._clear_output)

        vm = bar.addMenu("查看")
        vm.addAction("触发器…", lambda: self._open_editor("trigger"))
        vm.addAction("别名…", lambda: self._open_editor("alias"))
        vm.addAction("定时器…", lambda: self._open_editor("timer"))
        vm.addAction("宏…", lambda: self._open_editor("macro"))
        vm.addAction("节点图宏…", self._open_node_editor)
        vm.addAction("脚本…", lambda: self._open_editor("script"))
        vm.addAction("屏显屏蔽…", self._open_screen_block)
        vm.addSeparator()
        vm.addAction("命令速查", self._toggle_dock(self.commands_dock))
        vm.addAction("DSL 手册", self._toggle_dock(self.dsl_dock))
        vm.addAction("技能面板", self._toggle_dock(self.skills_dock))
        vm.addAction("状态", self._toggle_dock(self.state_dock))
        vm.addAction("快捷动作", self._toggle_dock(self.quick_dock))
        vm.addAction("移动控制", self._toggle_dock(self.move_dock))
        vm.addAction("宏控制", self._toggle_dock(self.macro_dock))
        vm.addAction("宏录制", self._toggle_dock(self.recorder_dock))
        vm.addAction("自动战斗", self._toggle_dock(self.combat_dock))
        vm.addAction("辅助修炼", self._toggle_dock(self.xiuxian_dock))
        vm.addAction("地图", self._toggle_dock(self.map_dock))
        vm.addAction("导航目的地", self._toggle_dock(self.nav_dock))
        vm.addAction("房间详情", self._toggle_dock(self.look_dock))
        vm.addAction("记事本", self._toggle_dock(self.notepad_dock))
        vm.addSeparator()
        tm_themes = vm.addMenu("🎨 主题")
        self._theme_actions: dict[str, QAction] = {}
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        current = cfg.ConfigManager.instance().get("theme", "night")
        for key, pal in PALETTES.items():
            act = QAction(f"{pal.get('name', key)}", self)
            act.setCheckable(True)
            act.setChecked(key == current)
            act.triggered.connect(lambda _=False, k=key: self._set_theme(k))
            theme_group.addAction(act)
            self._theme_actions[key] = act
            tm_themes.addAction(act)
        vm.addSeparator()
        vm.addAction("重置窗口布局", self._reset_layout)
        vm.addAction("世界地图", self._open_world_map)
        vm.addAction("全屏", self._toggle_fullscreen)

        tm = bar.addMenu("工具")
        tm.addAction("编码…", self._open_encoding)
        tm.addAction("fullme 2×2（开 4 次）", self._full_me_4)
        tm.addAction("自动拾取…", self._open_auto_pickup)
        tm.addAction("服务器环境变量…", self._open_env_settings)
        tm.addAction("快捷键设置…", lambda: ShortcutDialog(self).exec())
        tm.addAction("宏分享…", self._open_macro_share)

        am = bar.addMenu("账号")
        am.addAction("新建标签…", self._new_connection)
        am.addAction("关闭当前标签", self._close_current)
        am.addSeparator()
        am.addAction("客户端用户…", self._open_client_user)
        am.addAction("编辑账户信息…", self._manage_accounts)

        sm = bar.addMenu("脚本")
        sm.addAction("Lua 脚本…", lambda: self._open_editor("script"))

        hm = bar.addMenu("帮助")
        hm.addAction("检查更新…", self._check_update_now)
        hm.addAction("关于", self._about)
        hm.addAction("Lua 脚本手册", self._open_manual)

    def _build_toolbar(self) -> None:
        tb = QToolBar("自动化", self)
        tb.setObjectName("automation_toolbar")
        tb.setMovable(False)
        tb.addAction("🗺 世界地图", self._open_world_map)
        tb.addSeparator()
        tb.addAction("📋 触发器", lambda: self._open_editor("trigger"))
        tb.addAction("🔗 别名", lambda: self._open_editor("alias"))
        tb.addAction("⏱ 定时器", lambda: self._open_editor("timer"))
        tb.addAction("🎬 宏", lambda: self._open_editor("macro"))
        tb.addAction("🧩 节点图", self._open_node_editor)
        tb.addAction("📜 脚本", lambda: self._open_editor("script"))
        tb.addSeparator()
        self._trg_on_act = tb.addAction("🟢 触发器")
        self._trg_on_act.setCheckable(True)
        self._trg_on_act.setToolTip("触发器总开关：一键启用/停用全部触发器")
        self._trg_on_act.toggled.connect(self._toggle_trigger_master)
        self._trg_on_act.setChecked(bool(self.app.config.get("automation.trigger_on", True)))
        self._update_master_action(self._trg_on_act, "触发器", self._trg_on_act.isChecked(), notify=False)
        self._tmr_on_act = tb.addAction("🟢 定时器")
        self._tmr_on_act.setCheckable(True)
        self._tmr_on_act.setToolTip("定时器总开关：一键启用/停用全部定时器")
        self._tmr_on_act.toggled.connect(self._toggle_timer_master)
        self._tmr_on_act.setChecked(bool(self.app.config.get("automation.timer_on", True)))
        self._update_master_action(self._tmr_on_act, "定时器", self._tmr_on_act.isChecked(), notify=False)
        tb.addSeparator()
        tb.addAction("▶ 宏控制", self._toggle_dock(self.macro_dock))
        tb.addAction("🔍 查找", self._show_find)
        tb.addAction("📖 命令速查", self._toggle_dock(self.commands_dock))
        tb.addAction("📝 记事本", self._toggle_dock(self.notepad_dock))
        # 最右侧：屏显屏蔽便捷按钮（弹性占位把它推到工具栏右端）
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        tb.addAction("🛡 屏显屏蔽", self._open_screen_block)
        tb.addAction("🪣 自动拾取", self._open_auto_pickup)
        # 客户端用户快捷按钮（亮绿色边框，同账号菜单项）
        cu_btn = QToolButton()
        cu_btn.setText("👤 客户端用户")
        cu_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        cu_btn.setStyleSheet(
            "QToolButton { border: 2px solid #00ff00; border-radius: 4px; padding: 2px 8px; }"
            "QToolButton:hover { background-color: rgba(0,255,0,0.15); }"
            "QToolButton:pressed { background-color: rgba(0,255,0,0.25); }"
        )
        cu_btn.clicked.connect(self._open_client_user)
        tb.addWidget(cu_btn)
        self.addToolBar(tb)

    def _build_shortcuts(self) -> None:
        sc = ShortcutManager.instance()
        actions = {
            "find": self._show_find, "font": self._open_font, "fullscreen": self._toggle_fullscreen,
            "close_tab": self._close_current, "commands_panel": self._toggle_dock(self.commands_dock),
            "trigger_edit": lambda: self._open_editor("trigger"),
            "alias_edit": lambda: self._open_editor("alias"),
            "timer_edit": lambda: self._open_editor("timer"),
            "macro_edit": lambda: self._open_editor("macro"),
            "script_edit": lambda: self._open_editor("script"),
            "new_tab": self._new_connection,
            "disconnect": self._disconnect_current,
            "reconnect": self._reconnect_current,
            "quit": self.close,
            "clean": self._clean_screen,
            "clear_output": self._clear_output,
            "world_map": self._open_world_map,
            "next_tab": self._next_tab,
            "prev_tab": self._prev_tab,
            "find_next": self._find_next,
            "local_map": self._toggle_dock(self.map_dock),
        }
        for key, cb in actions.items():
            sc.register(key, cb)

    # ---- 当前标签 ----
    def _tab(self) -> AccountTab | None:
        return self.tabs.currentWidget() if isinstance(self.tabs.currentWidget(), AccountTab) else None

    @property
    def _cur(self) -> AccountTab | None:
        return self._tab()

    def _on_tab_changed(self, _index: int) -> None:
        tab = self._tab()
        self._cur_tab = tab
        if tab is None:
            return
        session = tab.session
        session.skills_dock = self.skills_dock_widget()
        self.skills_dock_widget().session = session
        self.quick_dock.widget().bind(session)
        self.move_dock.widget().bind(session)
        self.macro_dock.widget().bind(session)
        self.recorder_dock.widget().bind(session)
        self.combat_dock.widget().bind(session)
        self.xiuxian_dock.widget().bind(session)
        self.map_dock.widget().bind(session)
        self.nav_dock.widget().bind(session)
        self.look_dock.widget().bind(session)
        self.notepad_dock.widget().bind(session)
        self.skills_dock_widget().bind(session)
        self._subscribe_nav(session)
        tab._sync_channels()
        self._update_status(session)

    def skills_dock_widget(self) -> SkillsDock:
        return self.skills_dock.widget()

    def _subscribe_nav(self, session) -> None:
        """地图导航状态事件转发给地图面板更新 UI。"""
        self.app.bus.subscribe_pattern("nav.*", self.map_dock.widget().on_nav_state)
        self.app.bus.subscribe_pattern("nav.*", self.nav_dock.widget()._nav_state)

    def _update_status(self, session) -> None:
        st = session.state
        qi = f"{st.qi}/{st.max_qi}" if st.max_qi else ""
        name = st.name or session.cn_name() or ""
        self.status.set_state(f"{name} Lv{st.level or '?'} {qi}".strip())
        self.status.set_encoding(session.connection.encoding)
        self.status.set_timer_count(len(session.timers.list()))

    # ---- 事件 ----
    def _on_net_event(self, payload: dict) -> None:
        account = payload.get("account") or "?"
        if payload["event"] == "net.connecting":
            self.status.set_connection(f"{account} {payload.get('status', '连接中')}")
        elif payload["event"] == "net.connected":
            self.status.set_connection(f"{account} 已连接")
        else:
            self.status.set_connection(f"{account} 断开")

    def _on_state_changed(self, payload: dict) -> None:
        if payload.get("account") != getattr(self._cur_tab, "account_id", None):
            return
        self.state_dock.widget().update_state(payload.get("state"))
        self._update_status(self._cur_tab.session)

    def _on_combat(self, payload: dict) -> None:
        if payload.get("account") != getattr(self._cur_tab, "account_id", None):
            return
        self.state_dock.widget().update_combat(payload.get("data") or {})

    def _on_enemy(self, payload: dict) -> None:
        if payload.get("account") != getattr(self._cur_tab, "account_id", None):
            return
        self.state_dock.widget().update_enemy(payload.get("enemy") or {})

    def _on_buffs(self, payload: dict) -> None:
        if payload.get("account") != getattr(self._cur_tab, "account_id", None):
            return
        self.state_dock.widget().refresh_buffs(payload.get("buffs") or [])

    def _on_room(self, payload: dict) -> None:
        if payload.get("account") != getattr(self._cur_tab, "account_id", None):
            return
        self.move_dock.widget().set_exits(payload.get("exits") or [])

    def _on_fullme_grid(self, payload: dict) -> None:
        if payload.get("account") != getattr(self._cur_tab, "account_id", None):
            return
        from xkxclient.ui.fullme import FullmeGridWindow

        session = self._cur_tab.session
        w = FullmeGridWindow(session, urls=payload.get("urls") or [])
        w._debug_log = lambda msg: self.status.showMessage(str(msg)[:140], 8000)
        self._fullme_grid_win = w
        w.show()

    def _on_login_done(self, payload: dict) -> None:
        self.status.set_connection(f"{payload.get('account')} 登录成功")
        self._layout_diag = True
        if self._cur_tab is not None and self._cur_tab.account_id == payload.get("account"):
            self.skills_dock_widget().refresh()

    def _on_hongbao(self, payload: dict) -> None:
        if payload.get("account") != getattr(self._cur_tab, "account_id", None):
            return
        from xkxclient.ui.fullme import HongbaoWindow

        session = self._cur_tab.session
        w = HongbaoWindow(session, url=payload.get("url", ""))
        w._debug_log = lambda msg: self.status.showMessage(str(msg)[:120], 5000)
        self._hongbao_win = w
        w.show()

    def _on_status_message(self, payload: dict) -> None:
        msg = str(payload.get("message", ""))
        if msg:
            self.status.showMessage(msg, 5000)

    def _on_fullme(self, payload: dict) -> None:
        if payload.get("account") != getattr(self._cur_tab, "account_id", None):
            return
        from xkxclient.ui.fullme import FullmeWindow

        session = self._cur_tab.session
        w = FullmeWindow(session, source=payload.get("source", "manual"), url=payload.get("url", ""))
        w._debug_log = lambda msg: self.status.showMessage(str(msg)[:120], 5000)
        self._fullme_win = w
        w.show()

    def _on_throttle(self, payload: dict) -> None:
        if payload.get("account") != getattr(self._cur_tab, "account_id", None):
            return
        self.status.showMessage(str(payload.get("status", "命令限频")), 5000)

    def _on_macro_progress(self, payload: dict) -> None:
        if payload.get("account") != getattr(self._cur_tab, "account_id", None):
            return
        name = payload.get("name", "")
        self.macro_dock.widget().set_progress(f"运行: {name}")

    def _on_macro_wait(self, payload: dict) -> None:
        if payload.get("account") != getattr(self._cur_tab, "account_id", None):
            return
        prompt = payload.get("prompt") or "等待输入"
        self.status.showMessage(f"宏[{payload.get('name')}] {prompt}，请在输入框键入后回车", 0)

    # ---- 动作 ----
    def add_account_tab(self, account_id: str, session) -> None:
        tab = AccountTab(account_id, session)
        session.line_displayed.connect(tab.append_spans)
        session.channel_text.connect(tab.append_channel)
        self.tabs.addTab(tab, account_id)
        self.tabs.setCurrentWidget(tab)
        tab.input_line.setFocus()
        self._on_tab_changed(self.tabs.currentIndex())

    def _close_tab(self, index: int) -> None:
        tab = self.tabs.widget(index)
        if isinstance(tab, AccountTab):
            tab.session.close()
        self.tabs.removeTab(index)

    def _next_tab(self) -> None:
        n = self.tabs.count()
        if n > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % n)

    def _prev_tab(self) -> None:
        n = self.tabs.count()
        if n > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() - 1) % n)

    def _find_next(self) -> None:
        tab = self._tab()
        if tab is not None:
            tab.output.find_next()

    def _close_current(self) -> None:
        idx = self.tabs.currentIndex()
        if idx >= 0:
            self._close_tab(idx)

    def _disconnect_current(self) -> None:
        tab = self._tab()
        if tab:
            tab.session.close()

    def _reconnect_current(self) -> None:
        tab = self._tab()
        if tab and tab.session._connect_args:
            args = dict(tab.session._connect_args)
            tab.session.connect_to(**args)

    def _new_connection(self) -> None:
        from xkxclient.ui.login import LoginWindow

        self._login_window = LoginWindow(self.app, self)
        self._login_window.show()

    def _manage_accounts(self) -> None:
        """账号菜单：已存账号的增/删/改（写 accounts.json）。"""
        from xkxclient.ui.login import AccountManagerDialog

        AccountManagerDialog(cfg.ConfigManager.instance(), self).exec()

    def _open_client_user(self) -> None:
        """账号菜单：客户端用户（注册/登录/云同步设置与自动化）。"""
        from xkxclient.ui.clientuser import ClientUserDialog

        ClientUserDialog(self.app, self).exec()

    def _command_fill(self, name: str) -> None:
        tab = self._tab()
        if tab:
            tab.input_line.setText(name)
            tab.input_line.setFocus()

    def _command_send(self, name: str) -> None:
        tab = self._tab()
        if tab:
            tab.input_line.setText(name)
            tab.input_line._on_return()

    def _open_world_map(self) -> None:
        from xkxclient.ui.mapwindow import MapWindow

        self._map_win = MapWindow()
        self._map_win.show()

    def _set_theme(self, key: str) -> None:
        """切换全局主题并持久化。"""
        from PyQt6.QtWidgets import QApplication

        apply_theme(key)
        cfg.ConfigManager.instance().set("theme", key)
        for k, act in self._theme_actions.items():
            act.setChecked(k == key)

    def _about(self) -> None:
        AboutDialog(self).exec()

    def _open_manual(self) -> None:
        from xkxclient.ui.manual import LuaManualDialog

        self._manual_dialog = LuaManualDialog(self)
        self._manual_dialog.show()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _full_me_4(self) -> None:
        tab = self._tab()
        if tab:
            tab.session.request_full_4()

    def _open_env_settings(self) -> None:
        tab = self._tab()
        if tab:
            EnvSettingsDialog(tab.session, self).exec()

    def _open_screen_block(self) -> None:
        """查看→屏显屏蔽：管理主屏输出屏蔽规则（包含/正则）。"""
        from xkxclient.ui.screenblock import ScreenBlockDialog

        tab = self._tab()
        session = tab.session if tab else None
        ScreenBlockDialog(session, self).exec()

    def _open_auto_pickup(self) -> None:
        """工具栏→自动拾取：设置常驻自动拾取（物品中文名/英文名）。"""
        from xkxclient.ui.pickup import AutoPickupDialog

        tab = self._tab()
        session = tab.session if tab else None
        AutoPickupDialog(session, self).exec()

    def _toggle_chat(self) -> None:
        pass  # B5e：聊天栏恒开，无总开关

    def _toggle_trigger_master(self, on: bool) -> None:
        self.app.config.set("automation.trigger_on", on)
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            if isinstance(t, AccountTab):
                t.session.triggers.master_on = on
        self._update_master_action(self._trg_on_act, "触发器", on)

    def _toggle_timer_master(self, on: bool) -> None:
        self.app.config.set("automation.timer_on", on)
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            if isinstance(t, AccountTab):
                t.session.timers.master_on = on
        self._update_master_action(self._tmr_on_act, "定时器", on)

    def _update_master_action(self, act, name: str, on: bool, notify: bool = True) -> None:
        """总开关按钮图标 + 文字随状态切换，并状态栏提示。"""
        icon = "🟢" if on else "⚫"
        act.setText(f"{icon} {name}")
        if notify:
            self.status.showMessage(f"{name}已{'启用' if on else '停用'}", 3000)

    def _show_find(self) -> None:
        tab = self._tab()
        if tab:
            tab.show_find()

    def _open_font(self) -> None:
        tab = self._tab()
        if tab:
            tab.output._open_font_dialog()

    def _clean_screen(self) -> None:
        """清屏（Ctrl+Shift+C）：清空可见输出，历史保留。"""
        tab = self._tab()
        if tab:
            tab.output.clear()

    def _clear_output(self) -> None:
        """清空历史（Ctrl+L）：连同历史一并清空。"""
        tab = self._tab()
        if tab:
            tab.output.clear_history()

    def _open_encoding(self) -> None:
        from PyQt6.QtWidgets import QInputDialog

        tab = self._tab()
        if not tab:
            return
        enc, ok = QInputDialog.getItem(self, "编码", "选择编码", ["gbk", "utf-8", "big5"],
                                       0, False)
        if ok:
            tab.session.set_encoding(enc)

    def _toggle_dock(self, dock: QDockWidget):
        """菜单/工具栏切到某 dock：隐藏则显示；显示但被其它 dock 标签盖住则切到该标签，并闪烁提示。"""

        def toggle():
            if dock.isVisible():
                self._raise_dock(dock)
            else:
                dock.setVisible(True)
                self._raise_dock(dock)

        return toggle

    def _raise_dock(self, dock: QDockWidget) -> None:
        """把 dock 提到所在组合（tab 化）的前台，并闪烁 2-3 次提示位置。"""
        dock.raise_()
        self._flash_dock(dock, 3)

    def _flash_dock(self, dock: QDockWidget, times: int) -> None:
        """闪烁 dock 标题栏：交替高亮/正常背景色，times 次。"""
        from PyQt6.QtCore import QTimer

        title = dock.titleBarWidget() if dock.titleBarWidget() is not None else dock
        base = title.styleSheet()
        flash = "background:#5a7ad1; color:#ffffff; border-radius:4px; padding:2px 6px;"
        count = [0]

        def step():
            count[0] += 1
            title.setStyleSheet(flash if count[0] % 2 == 1 else base)
            if count[0] >= times * 2:
                title.setStyleSheet(base)

        t = QTimer(self)
        t.setInterval(160)
        t.timeout.connect(step)
        t.start()

    def _open_editor(self, kind: str) -> None:
        tab = self._tab()
        if not tab:
            return
        cls = {"trigger": editors.TriggerEditor, "alias": editors.AliasEditor,
               "timer": editors.TimerEditor, "macro": editors.MacroEditor}.get(kind)
        if kind == "script":
            from xkxclient.ui.scripteditor import ScriptEditor

            self._script_editor = ScriptEditor(tab.session, self)
            self._script_editor.show()
            return
        if cls:
            cls(tab.session, self).show()

    def _open_node_editor(self) -> None:
        """打开节点图宏编辑器（类 Visio 拖拽连线）。"""
        tab = self._tab()
        if not tab:
            return
        from xkxclient.ui.nodegraph import NodeGraphEditor
        self._node_editor = NodeGraphEditor(tab.session, "", None, self)
        self._node_editor.show()

    def _open_macro_share(self) -> None:
        """打开宏分享对话框：上传本地宏 / 下载共享宏。"""
        tab = self._tab()
        if not tab:
            return
        from xkxclient.ui.macroshare import MacroShareDialog
        self._macro_share = MacroShareDialog(tab.session, self)
        self._macro_share.show()

    # ---- 布局持久化 ----
    # 默认启动布局：以用户当前 dock 布局固化（8 方向各 dock 位置/尺寸）。
    _DEFAULT_LAYOUT = (
        "000000ff00000000fd0000000200000000000001440000038dfc0200000001fc000000420000038d000002b201000019fa000000000100000005fb000000140064006f0063006b005f5bfc822a76ee768457300100000000ffffffff0000014400fffffffb000000120064006f0063006b005f81ea52a8621865970100000000ffffffff000000f000fffffffb000000120064006f0063006b005f8f8552a94fee70bc0100000000ffffffff000000e600fffffffb000000100064006f0063006b005f5b8f5f5552360100000000ffffffff0000014400fffffffb0000000e0064006f0063006b005f573056fe0100000000ffffffff000000f000ffffff00000001000001220000038dfc0200000003fc0000004200000138000000c301000019fa000000010100000004fb000000120064006f0063006b005f5feb637752a84f5c0100000000ffffffff0000007800fffffffb000000100064006f0063006b005f5b8f63a7523601000000ffffffff0000010600fffffffb000000120064006f0063006b005f547d4ee4901f67e501000003fc000001040000004f00fffffffb000000160064006f0063006b005f00440053004c0020624b518c0100000000ffffffff0000004f00fffffffc0000017e00000164000000d801000019fa000000000100000003fb0000000e0064006f0063006b005f72b6600101000005a500000106000000dc00fffffffb000000120064006f0063006b005f628080fd9762677f0100000000ffffffff000000c800fffffffb000000120064006f0063006b005f623f95f48be660c50100000000ffffffff000000dc00fffffffb000000120064006f0063006b005f79fb52a863a7523601000002e6000000e9000000c100ffffff0000043d0000038d00000004000000040000000800000008fc00000001000000020000000100000024006100750074006f006d006100740069006f006e005f0074006f006f006c0062006100720100000000ffffffff0000000000000000"
    )

    def _restore_layout(self) -> None:
        raw = cfg.ConfigManager.instance().get("layout_state")
        if not (isinstance(raw, str) and raw):
            raw = self._DEFAULT_LAYOUT  # 无已存布局：用默认启动布局
        import base64

        try:
            self.restoreState(bytes.fromhex(raw))
        except (ValueError, TypeError):
            pass
        # 登录前主窗口未显示：仅隐藏浮动 dock（它是独立顶层窗口，停靠 dock 随
        # 主窗口隐藏不会显示）。避免 hide 停靠 dock 打乱 QMainWindow 停靠布局。
        if not self._docks_restored:
            for d in self.findChildren(QDockWidget):
                if d.isFloating():
                    d.setVisible(False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_docks_restored", False):
            self._docks_restored = True
            # 主窗口显示后恢复浮动 dock 可见性（停靠 dock 由主窗口 show 自动布局，
            # 不应重新 restoreState——窗口显示过程中重放布局会把 dock 排到未就绪的
            # 负坐标几何，导致控件被撑出窗口）。
            for d in self.findChildren(QDockWidget):
                if d.isFloating():
                    d.setVisible(True)
            # 恢复后强制重算布局，避免 dock 几何与窗口尺寸不同步
            QTimer.singleShot(0, self._relayout)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 兜底：布局偶尔失效时强制激活，保证控件随窗口缩放
        QTimer.singleShot(0, self._relayout)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        # 最大化/还原等窗口状态变化后，等窗口稳定再强制重算布局
        if event.type() == QEvent.Type.WindowStateChange:
            QTimer.singleShot(80, self._relayout)

    def _relayout(self) -> None:
        # 仅请求重算，不强制 activate（QMainWindow 布局内部管理 dock，
        # 强制 activate 可能在未稳定时固化错误几何）。
        self.updateGeometry()
        cw = self.centralWidget()
        if cw is not None:
            cw.updateGeometry()
            if cw.layout() is not None:
                cw.layout().invalidate()
                cw.layout().activate()
        for d in self.findChildren(QDockWidget):
            d.updateGeometry()
        self._heal_dock_layout()
        if getattr(self, "_layout_diag", False):
            try:
                import xkxclient.core.config as _c
                with open(_c.ConfigManager.instance().root / "layout_diag.log", "a", encoding="utf-8") as f:
                    f.write("state=%s max=%s win=%dx%d central=%s\n" % (
                        self.windowState(), self.isMaximized(), self.width(), self.height(),
                        self.centralWidget().geometry().getRect() if self.centralWidget() else None))
                    for d in self.findChildren(QDockWidget):
                        tabs = []
                        try:
                            tabs = [t.objectName() for t in self.tabifiedDockWidgets(d)]
                        except Exception:
                            pass
                        f.write("  dock[%s] float=%s vis=%s pos=%s geo=%s tab=%s\n" % (
                            d.objectName(), d.isFloating(), d.isVisible(),
                            d.pos().x(), d.pos().y(), d.geometry().getRect(), tabs))
            except Exception:
                pass

    def _heal_dock_layout(self) -> None:
        """自愈：停靠 dock 若被排到主窗口可视区外（负坐标），重新应用布局纠正。

        偶发竞态下 QMainWindow 可能把 dock 排到负坐标（控件被撑出窗口），
        检测到异常时重新 restoreState 一次，且只在本次显示期间纠正一次避免死循环。
        """
        if getattr(self, "_layout_healed", False):
            return
        if not self.isVisible():
            return
        vw = self.width()
        bad = False
        for d in self.findChildren(QDockWidget):
            if not d.isFloating() and d.isVisible():
                g = d.geometry()
                if g.x() < -80 or g.y() < -80 or g.x() > vw:
                    bad = True
                    break
        if not bad:
            return
        self._layout_healed = True
        try:
            raw = cfg.ConfigManager.instance().get("layout_state")
            if not (isinstance(raw, str) and raw):
                raw = self._DEFAULT_LAYOUT
            self.restoreState(bytes.fromhex(raw))
        except (ValueError, TypeError):
            pass
        QTimer.singleShot(50, self._relayout)

    def save_layout(self) -> None:
        state = self.saveState()
        cfg.ConfigManager.instance().set("layout_state", bytes(state).hex())

    def _reset_layout(self) -> None:
        """重置所有窗口布局为客户端默认布局：清除已存布局并恢复默认。"""
        cfg.ConfigManager.instance().set("layout_state", "")
        try:
            self.restoreState(bytes.fromhex(self._DEFAULT_LAYOUT))
        except (ValueError, TypeError):
            pass
        self.status.showMessage("窗口布局已重置为默认", 3000)

    def closeEvent(self, event) -> None:
        # 已在执行退出流程（托盘"退出"）时直接放行，不再询问
        if self.app.shutting_down:
            self.save_layout()
            super().closeEvent(event)
            return
        mode = cfg.ConfigManager.instance().get("close.mode", "ask")
        if mode == "always_quit":
            self._do_quit(event)
            return
        if mode == "always_tray":
            self._minimize_to_tray()
            event.ignore()
            return
        # 询问：退出 / 缩托盘 / 取消
        box = QMessageBox(self)
        box.setWindowTitle("关闭 EasyBXb")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("确定要关闭客户端吗？")
        remember_cb = QCheckBox("记住我的选择，不再提示", box)
        box.setCheckBox(remember_cb)
        btn_quit = box.addButton("关闭客户端", QMessageBox.ButtonRole.DestructiveRole)
        btn_tray = box.addButton("缩到系统托盘", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None or box.buttonRole(clicked) == QMessageBox.ButtonRole.RejectRole:
            event.ignore()
            return
        if clicked is btn_quit:
            if remember_cb.isChecked():
                cfg.ConfigManager.instance().set("close.mode", "always_quit")
            self._do_quit(event)
            return
        if clicked is btn_tray:
            if remember_cb.isChecked():
                cfg.ConfigManager.instance().set("close.mode", "always_tray")
            self._minimize_to_tray()
            event.ignore()

    def _show_shutdown_progress(self) -> None:
        """弹「正在关闭客户端，请稍候…」进度窗（见 _do_quit 说明）。

        shutdown 内部是嵌套事件循环，进度条动画定时器在其中照常触发。
        无登录会话时 shutdown 秒完成，此窗一闪即逝。
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("EasyBXb")
        dlg.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        dlg.setFixedSize(340, 96)
        dlg_lay = QVBoxLayout(dlg)
        dlg_lay.addStretch(1)
        tip = QLabel("正在关闭客户端，请稍候…")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dlg_lay.addWidget(tip)
        bar = QProgressBar(dlg)
        bar.setRange(0, 0)                       # 不定值：连续动画
        bar.setTextVisible(False)
        dlg_lay.addWidget(bar)
        dlg_lay.addStretch(1)
        dlg.setModal(True)
        dlg.show()
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()   # 先让等待窗完成绘制，再进入 shutdown 的阻塞
        anim = QTimer(dlg)
        anim.setInterval(150)
        anim.timeout.connect(lambda: bar.setValue((bar.value() + 1) % 100))
        anim.start()
        try:
            self.save_layout()
            self.app.shutdown()
        finally:
            anim.stop()
            dlg.close()

    def _do_quit(self, event) -> None:
        """真正退出：弹出「正在关闭」提示 → 保存布局 → 优雅登出 + 关会话 + 存配置 → 接受关闭。

        `app.shutdown()` 会阻塞最多 3s（优雅登出等服务器断开，见 Exit 提示），
        若直接执行进程会显得「点了退出没反应」。这里先弹一个带进度动画的
        等待窗，让用户看到反馈。
        """
        self._show_shutdown_progress()
        super().closeEvent(event)

    def _minimize_to_tray(self) -> None:
        """最小化到系统托盘（进程保留在后台）。"""
        self.hide()
        if hasattr(self, "tray"):
            self.tray.show()