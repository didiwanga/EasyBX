from __future__ import annotations

from PyQt6.QtCore import Qt, QStringListModel, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QStyle,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from xkxclient.ui.automationdock import arrow_move
from xkxclient.ui.commands import CommandStore
from xkxclient.ui.findbar import FindBar
from xkxclient.ui.output import OutputView
from xkxclient.ui.widgets import ChannelBar


class InputLine(QLineEdit):
    """B6 输入行：实时命令提示列表（输入即弹出）+ Tab 补全 + 发送回调 + 方向键移动。

    历史记录不再占用 ↑/↓（方向键已用于移动），改为 Ctrl+↑/Ctrl+↓ 翻页，
    或点击左侧「🔄」按钮弹出历史列表选择。
    """

    submit = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("输入命令，回车发送（Ctrl+↑/↓ 历史，Tab 补全；空输入时方向键移动）")
        self._store = CommandStore()
        self._history = None           # HistoryStore，由 bind 注入
        self._session = None           # 房间出口来源
        self._tab_idx = 0
        self._tab_cands: list[str] = []
        self._tab_prefix = ""
        self._suppress_popup = False
        # 实时提示列表：输入命令前缀时弹出，补全填入；popup NoFocus 不拦截回车/方向键
        self._completer = QCompleter(self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self._model = QStringListModel(self)
        self._completer.setModel(self._model)
        self._completer.activated.connect(self._fill_activated)
        popup = self._completer.popup()
        popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCompleter(self._completer)
        self.textChanged.connect(self._update_popup)
        self.returnPressed.connect(self._on_return)

        # 左侧历史按钮：弹出历史命令列表供选择（方向键已让位给移动）
        self.hist_btn = QPushButton(self)
        self.hist_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self.hist_btn.setToolTip("命令历史（Ctrl+↑/↓ 也可翻页）")
        self.hist_btn.setFixedSize(28, 28)
        self.hist_btn.clicked.connect(self._show_history_menu)
        self.hist_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def bind(self, history) -> None:
        self._history = history

    def bind_session(self, session) -> None:
        """绑定已登录会话，供空输入时的方向键移动使用。"""
        self._session = session

    def _on_return(self) -> None:
        text = self.text()
        if not text.strip():
            # 空输入（含仅空格）回车：发送一条空指令（回车），用于手动
            # 继续翻页/触发服务器空命令处理，而不是无动作
            if self._session is not None and self._session.connected:
                self._session.connection.send_line("")
            return
        self.submit.emit(text)
        if self._history is not None:
            self._history.record(text)
        self.clear()
        self._tab_cands = []

    def keyPressEvent(self, e: QKeyEvent) -> None:
        key = e.key()
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        # 历史翻页：Ctrl+↑ / Ctrl+↓（方向键已让位给移动）
        if ctrl and key == Qt.Key.Key_Up and self._history is not None:
            self.setText(self._history.back())
            self._tab_cands = []
            return
        if ctrl and key == Qt.Key.Key_Down and self._history is not None:
            self.setText(self._history.forward())
            self._tab_cands = []
            return
        if not ctrl and key in (
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
        ):
            if self._try_arrow_move(key):
                return
        if key == Qt.Key.Key_Tab:
            self._complete()
            return
        super().keyPressEvent(e)
        if key in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
            Qt.Key.Key_Backspace,
        ):
            self._tab_cands = []

    def _show_history_menu(self) -> None:
        """弹出历史命令列表，点击即填入输入框。"""
        if self._history is None:
            return
        items = self._history.peek()
        if not items:
            return
        menu = QMenu(self)
        for cmd in list(items)[-40:]:
            action = menu.addAction(cmd)
            action.triggered.connect(
                lambda _=False, c=cmd: self._pick_history(c)
            )
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))

    def _pick_history(self, cmd: str) -> None:
        self.setText(cmd)
        self._tab_cands = []
        self.setFocus()

    def _try_arrow_move(self, key) -> None:
        """仅在空输入 + 已登录时发送方向移动命令。"""
        if self._session is None or self.text() or not self._session.logged_in:
            return False
        cmd = arrow_move(getattr(self._session, "exits", []) or [], key)
        if cmd is None:
            return False
        self._session.send(cmd)
        return True

    def _played_back(self) -> bool:
        return self._history is not None and bool(self.text())

    def _prefix(self) -> str:
        text = self.text()
        seg = text.rsplit(";", 1)[-1]
        return seg.strip()

    # ---- 实时提示 ----
    def _update_popup(self, text: str) -> None:
        if self._suppress_popup:
            return
        prefix = self._prefix()
        if prefix:
            cands = self._candidates(prefix)
            self._model.setStringList(cands)
            if cands:
                self._completer.complete()
                return
        self._completer.popup().hide()

    def _fill_activated(self, cand: str) -> None:
        self._replace_segment(cand)
        self._completer.popup().hide()

    def _complete(self) -> None:
        prefix = self._prefix()
        if self._tab_prefix != prefix:
            self._tab_prefix = prefix
            self._tab_cands = self._candidates(prefix)
            self._tab_idx = 0
        if not self._tab_cands:
            return
        cand = self._tab_cands[self._tab_idx % len(self._tab_cands)]
        self._tab_idx += 1
        self._replace_segment(cand)

    def _candidates(self, prefix: str) -> list[str]:
        store_cands = self._store_cands(prefix)
        dirs = ["north", "south", "east", "west", "up", "down"]
        out = store_cands + [d for d in dirs if d.startswith(prefix)]
        out = list(dict.fromkeys(out))
        # B3c：`##` DSL 输入时 Tab 补全 sys./com./my.
        if prefix.startswith("##") and (not prefix[2:].strip() or any(
                n.startswith(prefix[2:].strip()) for n in ("sys", "com", "my"))):
            ns = ["sys.", "com.", "my."]
            dsl_head = prefix[2:].strip()
            out = out + [n for n in ns if n.startswith(dsl_head)]
        return list(dict.fromkeys(out))

    def _store_cands(self, prefix: str) -> list[str]:
        # 单例命令字典缓存，避免每次按键重建
        try:
            st = self._store
        except Exception:
            self._store = CommandStore()
            st = self._store
        return st.prefix_candidates(prefix)

    def _replace_segment(self, cand: str) -> None:
        text = self.text()
        idx = text.rfind(";")
        head = text[: idx + 1] if idx >= 0 else ""
        self._suppress_popup = True   # 避免 setText 触发的弹窗与补全竞争
        self.setText(head + cand)
        self._suppress_popup = False


class AccountTab(QWidget):
    """单账号标签页（D4）：主输出 + 查找栏 + 聊天栏 + 输入行。"""

    def __init__(self, account_id: str, session, parent=None) -> None:
        super().__init__(parent)
        self.account_id = account_id
        self.session = session

        self.output = OutputView(self)
        self.find_bar = FindBar(self.output, self)
        self.find_bar.hide()
        self.chat = ChannelBar(self)   # B5e：恒开，无总开关

        self.input_line = InputLine(self)
        self.input_line.bind(session.history)
        self.input_line.bind_session(session)
        self.input_line.submit.connect(self._submit)

        # 主区 = 输出 + 聊天栏
        self.main = QWidget(self)
        mlay = QVBoxLayout(self.main)
        mlay.setContentsMargins(0, 0, 0, 0)
        mlay.setSpacing(0)
        mlay.addWidget(self.output, 1)
        mlay.addWidget(self.find_bar)
        mlay.addWidget(self.chat)

        # 输入行 + 左侧历史按钮
        self.input_row = QWidget(self)
        row_lay = QHBoxLayout(self.input_row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(2)
        row_lay.addWidget(self.input_line.hist_btn)
        row_lay.addWidget(self.input_line, 1)
        # 输入行高度收紧：只让输出区占据多余空间，避免输入框上下出现空白
        est = max(self.input_line.sizeHint().height(),
                  self.input_line.hist_btn.sizeHint().height()) + 2
        self.input_row.setFixedHeight(est)

        self.split = QSplitter(Qt.Orientation.Vertical, self)
        self.split.addWidget(self.main)
        self.split.addWidget(self.input_row)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 0)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.split)

        self.output.new_trigger_requested.connect(self._new_trigger)
        self.output.filter_add_requested.connect(self._add_filter)
        self.output.search_requested.connect(lambda t: self.show_find())
        self.output.autopage_hit.connect(self._on_autopage)
        self.output.clicked_blank.connect(self._refocus_input)
        self.chat.channel_toggled.connect(self.session.set_channel)
        self._sync_channels()

    def _refocus_input(self) -> None:
        """点击输出区空白：焦点还给命令输入框。"""
        self.input_line.setFocus()

    # ---- 频道（B5e） ----
    def _sync_channels(self) -> None:
        self.chat.set_channels(self.session._channels)
        self.session.chat_open = self.chat.isVisible()

    def toggle_chat(self, visible: bool) -> None:
        self.chat.setVisible(visible)
        self.session.chat_open = visible
        if visible:
            self.chat._reset_height()

    # ---- 输出事件 ----
    def show_find(self) -> None:
        self.find_bar.show()
        self.find_bar.focus_edit()

    def _new_trigger(self, sel: str) -> None:
        from xkxclient.ui.editors import TriggerEditor

        dlg = TriggerEditor(self.session, self)
        if sel:
            try:
                from xkxclient.automation.trigger import Trigger

                tr = Trigger(name="from_select", pattern=sel)
                dlg.pattern_ed.setText(sel)
            except Exception:
                pass
        dlg.show()

    def _add_filter(self, sel: str) -> None:
        self.session.app.bus.publish("ui.message", account=self.account_id,
                                     message=f"已添加到过滤器: {sel}")

    def _on_autopage(self, _pages: int) -> None:
        # 自动翻页：发空命令继续分页（B5-3）
        self.session.connection.send_line("")

    # ---- 发送 ----
    def _submit(self, text: str) -> None:
        if not text.strip():
            return
        self.session.send(text)
        self.session.app.bus.publish("input.sent", account=self.account_id, text=text)

    def append_spans(self, spans: list, highlight: bool = False) -> None:
        self.output.append_spans(spans, highlight=highlight)

    def append_channel(self, name: str, spans: list, highlight: bool = False) -> None:
        self.chat.append(name, spans, highlight=highlight)

    def on_line(self, line: str) -> None:
        self.output.append_line(line)