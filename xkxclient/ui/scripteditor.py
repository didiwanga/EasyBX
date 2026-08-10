from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import QRegularExpression, Qt
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
)

from xkxclient.core.resources import PROJECT_ROOT

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
    """B8 Lua 脚本编辑器：语法高亮 + 保存/运行/导入 + 运行输出区。

    lupa 未安装时降级：仅编辑/保存，运行提示安装 lupa。
    """

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("Lua 脚本")
        self.resize(720, 560)

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Consolas", 10))
        self.hl = LuaHighlighter(self.editor.document())
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(140)

        run_btn = QPushButton("运行")
        save_btn = QPushButton("保存")
        imp_btn = QPushButton("导入 .lua…")
        run_btn.clicked.connect(self._run)
        save_btn.clicked.connect(self._save)
        imp_btn.clicked.connect(self._import)
        btns = QHBoxLayout()
        btns.addWidget(run_btn)
        btns.addWidget(save_btn)
        btns.addWidget(imp_btn)
        btns.addStretch(1)

        split = QSplitter(self)
        split.addWidget(self.editor)
        split.addWidget(self.output)
        split.setOrientation(Qt.Orientation.Vertical)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)

        lay = QVBoxLayout(self)
        lay.addLayout(btns)
        lay.addWidget(split, 1)

        self._script_dir = PROJECT_ROOT / "lua"
        self._script_dir.mkdir(parents=True, exist_ok=True)

    def _log(self, msg: str) -> None:
        self.output.appendPlainText(msg)

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "保存脚本", str(self._script_dir / "script.lua"),
                                              "Lua 脚本 (*.lua)")
        if path:
            Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")
            self._log(f"已保存 {path}")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入脚本", str(self._script_dir), "Lua 脚本 (*.lua)")
        if path:
            code = Path(path).read_text(encoding="utf-8")
            self.editor.setPlainText(code)
            dst = self._script_dir / Path(path).name
            dst.write_text(code, encoding="utf-8")
            self._log(f"已导入 {dst}")

    def _run(self) -> None:
        code = self.editor.toPlainText()
        try:
            import lupa  # noqa: F401
        except ImportError:
            self._log("lupa 未安装，无法执行 Lua（仅编辑/保存）。")
            return
        self._log("运行 Lua（lupa 运行时）…")
        try:
            import lupa
            runtime = lupa.LuaRuntime()
            bus = runtime.globals()
            bus.send = lambda cmd: self.session.send(cmd)
            bus.print = lambda *a: self._log(" ".join(str(x) for x in a))
            runtime.execute(code)
        except Exception as exc:
            self._log(f"运行错误: {exc}")