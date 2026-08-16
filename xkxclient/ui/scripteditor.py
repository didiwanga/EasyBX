from __future__ import annotations

import re

from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
)

_LUA_KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return", "then",
    "true", "until", "while",
}


class LuaHighlighter(QSyntaxHighlighter):
    def __init__(self, doc) -> None:
        super().__init__(doc)
        self._kw_fmt = QTextCharFormat()
        self._kw_fmt.setForeground(QColor("#569cd6"))
        self._str_fmt = QTextCharFormat()
        self._str_fmt.setForeground(QColor("#ce9178"))
        self._cm_fmt = QTextCharFormat()
        self._cm_fmt.setForeground(QColor("#6a9955"))
        self._num_fmt = QTextCharFormat()
        self._num_fmt.setForeground(QColor("#b5cea8"))

    def highlightBlock(self, text: str) -> None:
        for m in re.finditer(r"\"[^\"]*\"|'[^']*'|--.*$|\b\w+\b|\d+(?:\.\d+)?", text):
            tok = m.group(0)
            if tok.startswith("--"):
                self.setFormat(m.start(), len(tok), self._cm_fmt)
            elif tok.startswith(("\"", "'")):
                self.setFormat(m.start(), len(tok), self._str_fmt)
            elif tok.isdigit() or re.fullmatch(r"\d+\.\d+", tok):
                self.setFormat(m.start(), len(tok), self._num_fmt)
            elif tok in _LUA_KEYWORDS:
                self.setFormat(m.start(), len(tok), self._kw_fmt)


class ScriptEditor(QDialog):
    """B8/E8 Lua 脚本编辑器：脚本库管理 + 后台运行 + 运行日志。

    依赖 ScriptManager（scripts.json 持久化）。lupa 未安装时运行会友好报错，
    编辑/保存仍可用。
    """

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.manager = session.app.scripts()
        self._current: str | None = None
        self._runner = None
        self.setWindowTitle("Lua 脚本")
        self.resize(860, 620)

        # 左：脚本列表
        self.list_box = QListWidget()
        self.list_box.currentItemChanged.connect(self._on_select)

        new_btn = QPushButton("新建")
        del_btn = QPushButton("删除")
        new_btn.clicked.connect(self._on_new)
        del_btn.clicked.connect(self._on_delete)
        left_btns = QHBoxLayout()
        left_btns.addWidget(new_btn)
        left_btns.addWidget(del_btn)
        left_btns.addStretch(1)

        left = QVBoxLayout()
        left.addWidget(QLabel("脚本"))
        left.addWidget(self.list_box, 1)
        left.addLayout(left_btns)

        # 右：属性 + 编辑器 + 输出 + 操作
        self.name_ed = QLineEdit()
        self.name_ed.setPlaceholderText("脚本名称")
        self.enabled_chk = QCheckBox("启用（登录后自动运行）")
        self.enabled_chk.toggled.connect(self._on_enabled_toggled)
        self.timeout_sp = QSpinBox()
        self.timeout_sp.setRange(1000, 86400000)
        self.timeout_sp.setValue(3600000)
        self.timeout_sp.setSuffix(" ms")

        prop = QHBoxLayout()
        prop.addWidget(QLabel("名称"))
        prop.addWidget(self.name_ed, 1)
        prop.addWidget(self.enabled_chk)
        prop.addWidget(QLabel("超时"))
        prop.addWidget(self.timeout_sp)

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Consolas", 10))
        self.hl = LuaHighlighter(self.editor.document())

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(160)

        run_btn = QPushButton("运行")
        stop_btn = QPushButton("停止")
        pause_btn = QPushButton("暂停")
        save_btn = QPushButton("保存")
        imp_btn = QPushButton("导入 .lua…")
        exp_btn = QPushButton("导出 .lua…")
        run_btn.clicked.connect(self._run)
        stop_btn.clicked.connect(self._stop)
        pause_btn.clicked.connect(self._toggle_pause)
        save_btn.clicked.connect(self._save)
        imp_btn.clicked.connect(self._import)
        exp_btn.clicked.connect(self._export)
        btns = QHBoxLayout()
        btns.addWidget(run_btn)
        btns.addWidget(stop_btn)
        btns.addWidget(pause_btn)
        btns.addWidget(save_btn)
        btns.addWidget(imp_btn)
        btns.addWidget(exp_btn)
        btns.addStretch(1)

        right = QVBoxLayout()
        right.addLayout(prop)
        right.addWidget(self.editor, 3)
        right.addLayout(btns)
        right.addWidget(self.output)

        split = QSplitter(self)
        left_w = _layout_widget(left)
        split.addWidget(left_w)
        right_w = _layout_widget(right)
        split.addWidget(right_w)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)

        self.cur_btn = pause_btn
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)

        self._refresh_list()

    # ---- 列表 ----
    def _refresh_list(self, select: str | None = "keep") -> None:
        current = select
        if select == "keep":
            current = self._current
        names = self.manager.list()
        self.list_box.clear()
        for n in sorted(names):
            self.list_box.addItem(n)
        if current in names:
            self.list_box.setCurrentRow(names.index(current))
        elif names:
            self.list_box.setCurrentRow(0)
        else:
            self._load_script(None)

    def _on_select(self, item, _prev) -> None:
        self._load_script(None if item is None else item.text())

    def _load_script(self, name: str | None) -> None:
        self._detach_runner()
        self._current = name
        if name is None:
            self.name_ed.clear()
            self.editor.setPlainText("")
            self.enabled_chk.setChecked(False)
            self.timeout_sp.setValue(3600000)
            self.output.setPlainText("")
            return
        d = self.manager.get(name)
        if not d:
            return
        self.name_ed.setText(name)
        self.editor.setPlainText(d.get("code") or "")
        self.enabled_chk.setChecked(bool(d.get("enabled", False)))
        self.timeout_sp.setValue(int((d.get("timeout") or 60) * 1000))
        self.output.setPlainText("")

    # ---- 操作 ----
    def _on_new(self) -> None:
        name, ok = QInputDialog.getText(self, "新建脚本", "脚本名称：")
        if ok and name.strip():
            self._save_current()
            self.manager.save(name.strip(), "")
            self._current = name.strip()
            self._refresh_list(select=name.strip())

    def _on_delete(self) -> None:
        if not self._current:
            return
        if QMessageBox.question(self, "删除脚本", "删除 %s？" % self._current) != QMessageBox.StandardButton.Yes:
            return
        self.manager.remove(self._current)
        self._detach_runner()
        self._refresh_list(select=None)

    def _save(self) -> None:
        self._save_current()

    def _save_current(self) -> None:
        name = self.name_ed.text().strip()
        if not name:
            QMessageBox.information(self, "保存", "请输入脚本名称")
            return
        if self._current and name != self._current:
            self.manager.remove(self._current)
        self.manager.save(name, self.editor.toPlainText(), timeout=self.timeout_sp.value() / 1000.0,
                          enabled=self.enabled_chk.isChecked())
        self._current = name
        self._refresh_list(select=name)

    def _on_enabled_toggled(self, on: bool) -> None:
        if self._current:
            self.manager.set_enabled(self._current, on)

    def _login_running_guard(self):
        if self.session is None:
            QMessageBox.information(self, "运行", "当前无已登录账号会话，无法运行脚本")
            return False
        return True

    def _run(self) -> None:
        if not self._current:
            return
        self._save_current()
        if not self._login_running_guard():
            return
        self._detach_runner()
        runner = self.manager.run(self.session, self._current)
        if runner is None:
            self.output.appendPlainText("启动失败：脚本不存在或已在运行")
            return
        self._runner = runner
        runner.log.connect(self.output.appendPlainText)
        runner.finished.connect(self._on_finished)
        self.output.appendPlainText(">>> 运行 %s" % self._current)

    def _on_finished(self, ok: bool, detail: str) -> None:
        self.output.appendPlainText(">>> 结束" + ("" if ok else "（错误）"))
        if detail:
            self.output.appendPlainText(detail)
        self._runner = None

    def _stop(self) -> None:
        if self._current:
            self.manager.stop(self.session.account_id, self._current)

    def _toggle_pause(self) -> None:
        r = self.manager.runner(self.session.account_id, self._current) if self._current else None
        if r is None:
            return
        if r.request_data("_paused"):
            self.manager.resume(self.session.account_id, self._current)
            self.cur_btn.setText("暂停")
        else:
            self.manager.pause(self.session.account_id, self._current)
            self.cur_btn.setText("继续")

    def _detach_runner(self) -> None:
        if self._runner is not None:
            try:
                self._runner.finished.disconnect(self._on_finished)
            except Exception:
                pass
            self._runner = None

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入脚本", "", "Lua 脚本 (*.lua)")
        if path:
            try:
                name = self.manager.import_lua(path)
            except OSError as exc:
                QMessageBox.warning(self, "导入失败", str(exc))
                return
            self._current = name
            self.name_ed.setText(name)      # 同步名字框，避免 _save_current 存成旧名
            self.editor.setPlainText(self.manager.code_of(name))
            self._refresh_list(select=name)

    def _export(self) -> None:
        if not self._current:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出脚本",
                                              str(self._current + ".lua"), "Lua 脚本 (*.lua)")
        if path:
            self.manager.export_lua(self._current, path)

    def closeEvent(self, event) -> None:
        self._detach_runner()
        super().closeEvent(event)


def _layout_widget(layout) -> None:
    from PyQt6.QtWidgets import QWidget

    w = QWidget()
    w.setLayout(layout)
    return w