from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from xkxclient.core.resources import PROJECT_ROOT


class LuaManualDialog(QDialog):
    """完整 Lua 脚本手册（帮助菜单）：从 resources/lua_manual.md 加载并用 Qt Markdown 渲染。

    提供关键词搜索下一个/上一个跳转；附加脚本编辑器入口可快速打开编辑器。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("EasyBXb - Lua 脚本手册")
        self.resize(760, 640)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索手册关键字，回车=下一个…")
        self.prev_btn = QPushButton("↑ 上一个")
        self.next_btn = QPushButton("↓ 下一个")
        self.manual_btn = QPushButton("打开脚本编辑器")
        self.count_label = QLabel("")

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)

        self.search.textChanged.connect(self._on_search)
        self.next_btn.clicked.connect(self._next)
        self.prev_btn.clicked.connect(self._prev)
        self.manual_btn.clicked.connect(self._open_editor)
        self.search.returnPressed.connect(self._next)

        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.prev_btn)
        top.addWidget(self.next_btn)
        top.addWidget(self.manual_btn)
        top.addWidget(self.count_label)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)

        self._matches: list = []
        self._pos = -1
        self._load()

    def _load(self) -> None:
        path = PROJECT_ROOT / "resources" / "lua_manual.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        self.view.setMarkdown(text or "# Lua 脚本手册\n\n> 手册文件缺失：请检查 resources/lua_manual.md")

    def _on_search(self, text: str) -> None:
        kw = text.strip()
        self._matches = []
        self._pos = -1
        if not kw:
            self.count_label.setText("")
            return
        doc = self.view.toPlainText()
        start = 0
        while True:
            idx = doc.lower().find(kw.lower(), start)
            if idx < 0:
                break
            self._matches.append(idx)
            start = idx + 1
        self.count_label.setText(f"命中 {len(self._matches)}")
        if self._matches:
            self._jump(0)

    def _jump(self, idx: int) -> None:
        if not self._matches:
            return
        self._pos = idx % len(self._matches)
        cur = self._matches[self._pos]
        cursor = self.view.textCursor()
        cursor.setPosition(cur)
        self.view.setTextCursor(cursor)
        self.view.ensureCursorVisible()
        self.view.setFocus()
        self.count_label.setText(
            f"{self._pos + 1}/{len(self._matches)}")

    def _next(self) -> None:
        if self._matches:
            self._jump(self._pos + 1)

    def _prev(self) -> None:
        if self._matches:
            self._jump(self._pos - 1)

    def _open_editor(self) -> None:
        parent = self.parent()
        self.close()
        if parent is not None:
            from xkxclient.ui.scripteditor import ScriptEditor
            session = getattr(parent, "_cur_tab", None)
            if session is not None:
                session = session.session
            if session is None:
                # 无标签页（未登录任何账号）：ScriptEditor 首行就访问 session.app 会崩
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.information(parent, "Lua 脚本", "请先登录一个账号再打开脚本编辑器。")
                return
            w = ScriptEditor(session, parent)
            w.show()