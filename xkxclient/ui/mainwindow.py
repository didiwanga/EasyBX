from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import QDockWidget, QMainWindow, QMessageBox, QTabWidget, QToolBar, QWidget

from xkxclient.core import config as cfg
from xkxclient.core import resources
from xkxclient.core.shortcuts import ShortcutManager
from xkxclient.ui import editors
from xkxclient.ui.about import AboutDialog
from xkxclient.ui.automationdock import (MacroControlDock, MacroRecorderDock,
                                         MoveControlDock, QuickActionsDock)
from xkxclient.ui.combatdock import CombatAssistDock
from xkxclient.ui.commands import CommandPanel, CommandStore
from xkxclient.ui.dslmanual import DslManualPanel
from xkxclient.ui.lookdock import LookDock
from xkxclient.ui.mapdock import MapDock
from xkxclient.ui.settings import EnvSettingsDialog, ShortcutDialog
from xkxclient.ui.skillsdock import SkillsDock
from xkxclient.ui.statusdock import StateDock
from xkxclient.ui.statusbar import StatusBar
from xkxclient.ui.tabs import AccountTab
from xkxclient.ui.theme import PALETTES, apply as apply_theme
from xkxclient.ui.tray import AppTray
from xkxclient.ui.xiuxiandock import XiuxianDock


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
        self.look_dock = self._make_dock("房间详情", LookDock(None))
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
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.look_dock)

        self._cur_tab: AccountTab | None = None
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
        self.app.bus.subscribe("macro.start", self._on_macro_progress)
        self.app.bus.subscribe("macro.end", self._on_macro_progress)
        self.app.bus.subscribe("macro.wait_input", self._on_macro_wait)
        self.app.bus.subscribe("net.throttle", self._on_throttle)

        self.tray = AppTray(self)
        self.tray.show()

        self.commands_dock.widget().fill_requested.connect(self._command_fill)
        self.commands_dock.widget().send_requested.connect(self._command_send)

        self._restore_layout()

    # ---- 装配工具 ----
    def _make_dock(self, title: str, widget: QWidget) -> QDockWidget:
        dock = QDockWidget(title, self)
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
        fm.addAction("退出", self.close)

        em = bar.addMenu("编辑")
        em.addAction("查找…", self._show_find)
        em.addAction("字体设置…", self._open_font)
        em.addAction("清洁画面", self._clean_screen)
        em.addAction("清空输出", self._clear_output)

        vm = bar.addMenu("查看")
        vm.addAction("触发器…", lambda: self._open_editor("trigger"))
        vm.addAction("别名…", lambda: self._open_editor("alias"))
        vm.addAction("定时器…", lambda: self._open_editor("timer"))
        vm.addAction("宏…", lambda: self._open_editor("macro"))
        vm.addAction("脚本…", lambda: self._open_editor("script"))
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
        vm.addAction("房间详情", self._toggle_dock(self.look_dock))
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
        vm.addAction("世界地图", self._open_world_map)
        vm.addAction("全屏", self._toggle_fullscreen)

        tm = bar.addMenu("工具")
        tm.addAction("编码…", self._open_encoding)
        tm.addAction("fullme 2×2（开 4 次）", self._full_me_4)
        tm.addAction("服务器环境变量…", self._open_env_settings)
        tm.addAction("快捷键设置…", lambda: ShortcutDialog(self).exec())

        am = bar.addMenu("账号")
        am.addAction("新建标签…", self._new_connection)
        am.addAction("关闭当前标签", self._close_current)

        sm = bar.addMenu("脚本")
        sm.addAction("Lua 脚本…", lambda: self._open_editor("script"))

        hm = bar.addMenu("帮助")
        hm.addAction("关于", self._about)
        hm.addAction("脚本 API 文档", self._api_doc)

    def _build_toolbar(self) -> None:
        tb = QToolBar("自动化", self)
        tb.setObjectName("automation_toolbar")
        tb.setMovable(False)
        tb.addAction("📋 触发器", lambda: self._open_editor("trigger"))
        tb.addAction("🔗 别名", lambda: self._open_editor("alias"))
        tb.addAction("⏱ 定时器", lambda: self._open_editor("timer"))
        tb.addAction("🎬 宏", lambda: self._open_editor("macro"))
        tb.addAction("📜 脚本", lambda: self._open_editor("script"))
        tb.addSeparator()
        self._trg_on_act = tb.addAction("✓ 触发器")
        self._trg_on_act.setCheckable(True)
        self._trg_on_act.setChecked(bool(self.app.config.get("automation.trigger_on", True)))
        self._trg_on_act.toggled.connect(self._toggle_trigger_master)
        self._tmr_on_act = tb.addAction("✓ 定时器")
        self._tmr_on_act.setCheckable(True)
        self._tmr_on_act.setChecked(bool(self.app.config.get("automation.timer_on", True)))
        self._tmr_on_act.toggled.connect(self._toggle_timer_master)
        tb.addSeparator()
        tb.addAction("▶ 宏控制", lambda: self.macro_dock.show())
        tb.addAction("🔍 查找", self._show_find)
        tb.addAction("📖 命令速查", lambda: self.commands_dock.show())
        self.addToolBar(tb)

    def _build_shortcuts(self) -> None:
        sc = ShortcutManager.instance()
        actions = {
            "find": self._show_find, "font": self._open_font, "fullscreen": self._toggle_fullscreen,
            "close_tab": self._close_current, "commands_panel": lambda: self.commands_dock.show(),
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
            "local_map": lambda: self.map_dock.show(),
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
        self.look_dock.widget().bind(session)
        self.skills_dock_widget().bind(session)
        self._subscribe_nav(session)
        tab._sync_channels()
        self._update_status(session)

    def skills_dock_widget(self) -> SkillsDock:
        return self.skills_dock.widget()

    def _subscribe_nav(self, session) -> None:
        """地图导航状态事件转发给地图面板更新 UI。"""
        self.app.bus.subscribe_pattern("nav.*", self.map_dock.widget().on_nav_state)

    def _update_status(self, session) -> None:
        st = session.state
        qi = f"{st.qi}/{st.max_qi}" if st.max_qi else ""
        self.status.set_state(f"{st.name or ''} Lv{st.level or '?'} {qi}".strip())
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
        w.show()

    def _on_login_done(self, payload: dict) -> None:
        self.status.set_connection(f"{payload.get('account')} 登录成功")
        if self._cur_tab is not None and self._cur_tab.account_id == payload.get("account"):
            self.skills_dock_widget().refresh()

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

        LoginWindow(self.app, self).show()

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

    def _api_doc(self) -> None:
        QMessageBox.information(self, "脚本 API",
                                "send(cmd) / print(text)\n"
                                "触发器/别名/定时器/宏 由编辑器管理。\n"
                                "事件总线事件前缀：net.* state.* GMCP.* channel.* look.parsed map.*")

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

    def _toggle_chat(self) -> None:
        pass  # B5e：聊天栏恒开，无总开关

    def _toggle_trigger_master(self, on: bool) -> None:
        self.app.config.set("automation.trigger_on", on)
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            if isinstance(t, AccountTab):
                t.session.triggers.master_on = on

    def _toggle_timer_master(self, on: bool) -> None:
        self.app.config.set("automation.timer_on", on)
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            if isinstance(t, AccountTab):
                t.session.timers.master_on = on

    def _show_find(self) -> None:
        tab = self._tab()
        if tab:
            tab.show_find()

    def _open_font(self) -> None:
        tab = self._tab()
        if tab:
            tab.output._open_font_dialog()

    def _clean_screen(self) -> None:
        """清洁画面（Ctrl+Shift+C）：清空可见输出，历史保留。"""
        tab = self._tab()
        if tab:
            tab.output.clear()

    def _clear_output(self) -> None:
        """清空输出（Ctrl+L）：连同历史一并清空。"""
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
        def toggle():
            dock.setVisible(not dock.isVisible())

        return toggle

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

    # ---- 布局持久化 ----
    # 默认启动布局：以用户当前 dock 布局固化（8 方向各 dock 位置/尺寸）。
    _DEFAULT_LAYOUT = (
        "000000ff00000000fd000000020000000000000144000002ddfc0200000001fc00000042000002dd000002b101000019fa000000000100000004fb000000120064006f0063006b005f81ea52a8621865970100000000ffffffff000000f000fffffffb000000120064006f0063006b005f8f8552a94fee70bc0100000000ffffffff000000e600fffffffb000000100064006f0063006b005f5b8f5f5552360100000000ffffffff0000014400fffffffb0000000e0064006f0063006b005f573056fe0100000000ffffffff000000f000ffffff0000000100000106000002ddfc0200000003fc00000042000000dd000000dd01000019fa000000000100000004fb000000120064006f0063006b005f5feb637752a84f5c0100000000ffffffff0000009600fffffffb000000100064006f0063006b005f5b8f63a752360100000000ffffffff0000010600fffffffb000000120064006f0063006b005f547d4ee4901f67e501000003fc000001040000004f00fffffffb000000160064006f0063006b005f00440053004c0020624b518c0100000000ffffffff0000004f00fffffffc0000012300000137000000d801000019fa000000010100000003fb0000000e0064006f0063006b005f72b6600101000005a500000106000000dc00fffffffb000000120064006f0063006b005f628080fd9762677f0100000000ffffffff000000c800fffffffb000000120064006f0063006b005f623f95f48be660c50100000000ffffffff000000dc00fffffffb000000120064006f0063006b005f79fb52a863a75236010000025e000000c1000000c100ffffff000002ae000002dd00000004000000040000000800000008fc00000001000000020000000100000024006100750074006f006d006100740069006f006e005f0074006f006f006c0062006100720100000000ffffffff0000000000000000"
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

    def save_layout(self) -> None:
        state = self.saveState()
        cfg.ConfigManager.instance().set("layout_state", bytes(state).hex())

    def closeEvent(self, event) -> None:
        self.save_layout()
        self.app.shutdown()
        super().closeEvent(event)