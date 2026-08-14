from __future__ import annotations

import re
from collections import deque

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeyEvent, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocumentFragment
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFontComboBox,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from xkxclient.core import config as cfg
from xkxclient.net.ansi import Span

# 自动分页提示行（B5-3）：`== 未完继续 34% ==`
_PAGE_RE = re.compile(r"==\s*未完继续\s+(\d+)\s*%\s*==")
_MAX_AUTO_PAGES = 20

# B5：显示窗口上限 5000 行；历史 20000 条（回看更早历史时加载）
_WINDOW_MAX = 5000
_HISTORY_MAX = 20000

# 触发器命中行的整行高亮背景（深金色，避免与普通前景/背景冲突）
_HIGHLIGHT_BG = QColor("#3d3410")


class OutputView(QPlainTextEdit):
    """主输出窗口（wiki B5 / B5d / B5-2）。

    等宽 + 不换行 + 空格等宽约束；ANSI 逐段着色；follow-mode 自动滚屏（B5d）；
    自动分页（== 未完继续 N% ==）；折叠行（B5）；右键菜单；字体设置。
    行数两级：显示窗口 5000 行，历史队列 20000 条，回看顶部自动加载更早历史。
    """

    new_trigger_requested = pyqtSignal(str)
    screen_block_add_requested = pyqtSignal(str)
    search_requested = pyqtSignal(str)
    command_fill_requested = pyqtSignal(str)   # 右键：填写命令（仅填入命令框）
    command_send_requested = pyqtSignal(str)   # 右键：发送命令（填入并执行）
    look_send_requested = pyqtSignal(str)      # 右键：直接发送 look + 选中文本
    ask_fill_requested = pyqtSignal(str)       # 右键：填入 ask + 选中文本 + about
    notepad_add_requested = pyqtSignal(object) # 右键：添加到记事本（QTextDocumentFragment）
    autopage_hit = pyqtSignal(int)
    search_hits_updated = pyqtSignal(list)   # B5：当前命中行列表 [(行号1基, 全文)]，供分屏显示
    clicked_blank = pyqtSignal()             # 点击输出区空白 → 焦点还给命令输入框

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(0)          # 手动裁剪（B5：5000 窗口易裁剪，避免 QPTE 静默丢块）
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._following = True
        self._new_since_pause = 0
        self._auto_paging = True
        self._page_count = 0
        self._fold_counts: dict[str, int] = {}      # key -> 累计次数
        self._last_fold_key: str | None = None
        self._last_block_was_fold = False
        self._search_matches: list[tuple[int, int]] = []
        self._search_index = -1
        self._history: deque[str] = deque(maxlen=_HISTORY_MAX)   # 已移出显示窗口的更早行（B5）
        self._view_all = False           # True：正处于回看历史（doc 可超 5000 行）

        self._apply_font(cfg.ConfigManager.instance().get("font", {"family": "SimHei", "size": 12}))

        # 浮动「新消息 N」按钮（B5d）
        self.new_btn = QPushButton("", self)
        self.new_btn.setVisible(False)
        self.new_btn.setMaximumWidth(120)
        self.new_btn.clicked.connect(self.jump_to_bottom)
        self.new_btn.setStyleSheet(
            "QPushButton { background:#1e3a5f; color:#9fd0ff; border:1px solid #2f6a9c;"
            " border-radius:4px; padding:3px 8px; font-weight:bold; }"
        )

    # ---------- 字体 ----------
    def _apply_font(self, spec: dict) -> None:
        f = QFont(spec.get("family", "Consolas"), int(spec.get("size", 12)))
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(f)

    def mouseReleaseEvent(self, e) -> None:
        """点击输出区（读only）：焦点还给命令输入框，不拦截右键菜单/拖选。"""
        super().mouseReleaseEvent(e)
        if e.button() == Qt.MouseButton.LeftButton and not self.textCursor().hasSelection():
            self.clicked_blank.emit()

    def set_font_spec(self, spec: dict) -> None:
        self._apply_font(spec)
        cfg.ConfigManager.instance().set("font", spec)

    # ---------- 追加 ----------
    def append_spans(self, spans: list[Span], highlight: bool = False) -> None:
        self._insert_blocks([(spans, None)], highlight=highlight)

    def append_line(self, text: str) -> None:
        self._insert_blocks([([Span(text)], None)])

    def write(self, text: str) -> None:
        self.append_line(text)

    def append_fold_line(self, key: str, display: str, color: str = "888888") -> None:
        """折叠行（B5）：相同报告行反复出现时合并显示为一行 + 计数，点击展开。"""
        n = self._fold_counts.get(key, 0) + 1
        self._fold_counts[key] = n
        if self._last_fold_key == key and n > 1:
            # 覆盖上一行文本为 `display (×N)`
            cur = QTextCursor(self.document().lastBlock())
            cur.movePosition(QTextCursor.MoveOperation.StartOfLine, QTextCursor.MoveMode.KeepAnchor)
            cur.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            cur.insertText(f"{display} (×{n})")
        else:
            self._insert_blocks([([Span(display, fg=color)], key)])
        self._last_fold_key = key

    def _insert_blocks(self, blocks: list[tuple[list[Span], str | None]], highlight: bool = False) -> None:
        """追加若干行。B5d：未跟随（暂停）时不滚动，只累计浮动按钮计数。
        B5：若处于跟随窗口模式（非回看），插入后把超出 5000 行的最顶行移入历史队列。
        """
        sb = self.verticalScrollBar()
        was_following = self._following or self._is_at_bottom()
        pos = sb.value()
        # 用独立文档光标插入，不影响当前活动光标（避免滚回可见触发自动回底）
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for spans, fold_key in blocks:
            if highlight:
                bg = QTextBlockFormat()
                bg.setBackground(_HIGHLIGHT_BG)
                cursor.setBlockFormat(bg)
            for s in spans:
                fmt = QTextCharFormat()
                if s.bold:
                    fmt.setFontWeight(QFont.Weight.Bold)
                if s.fg:
                    fmt.setForeground(QColor("#" + s.fg))
                if s.bg:
                    fmt.setBackground(QColor("#" + s.bg))
                cursor.insertText(s.text, fmt)
            if fold_key is not None and spans:
                n = self._fold_counts.get(fold_key, 1)
                fmt = QTextCharFormat()
                fmt.setForeground(QColor("#" + (spans[0].fg or "888888")))
                cursor.insertText(f" (×{n})", fmt)
            cursor.insertText("\n")
            if highlight:
                # 换行已创建下一个块：重置块格式为空，避免高亮背景继承给后续所有行
                cursor.setBlockFormat(QTextBlockFormat())
        if was_following:
            self.setTextCursor(cursor)
            self.ensureCursorVisible()
            self._trim_history()
        else:
            # 非跟随（回看历史）期间不 trim：避免删除顶部行导致滚动条持续扰动，
            # 待用户回到底部恢复跟随后再统一裁剪。
            self._new_since_pause += 1
            self._update_new_btn()
        for spans, _ in blocks:
            if self._auto_paging and spans:
                self._check_paging(spans[0].text)

    def _trim_history(self) -> None:
        """B5：显示窗口 5000 行。把文档最顶行移入 _history，保留最新 _WINDOW_MAX 行。"""
        doc = self.document()
        while doc.blockCount() - 1 > _WINDOW_MAX:      # blockCount 含末尾空块
            blk = doc.firstBlock()
            if not blk.isValid() or not blk.length():
                break
            self._history.append(blk.text())
            cur = QTextCursor(blk)
            cur.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor)
            cur.removeSelectedText()
        self._view_all = False if doc.blockCount() - 1 <= _WINDOW_MAX else self._view_all

    def _load_earlier(self) -> None:
        """B5d：回看到达文档顶部并且历史队列仍有更早行时，把更多历史插入顶部。
        插入后用滚动条偏移补偿视觉位置（等效“向上扩展内容”），保持当前可见内容原位不动。
        """
        if not self._history or self._view_all:
            return
        self._view_all = True
        lines = list(self._history)
        self._history.clear()
        sb = self.verticalScrollBar()
        old_value = sb.value()
        old_h = self.document().size().height()
        for text_line in lines:
            cur = QTextCursor(self.document())
            cur.movePosition(QTextCursor.MoveOperation.Start)
            cur.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            cur.insertText(text_line + "\n")   # 插到最前：每行插在段落开头的行上方
        new_h = self.document().size().height()
        # 顶部插入 N 行后，文档变高；把滚动值同步下移插入高度，让原本可见的内容保持原位
        sb.setValue(int(old_value + (new_h - old_h)))

    def _jump_top_to_bottom(self) -> None:
        """Ctrl+End（或「到底部」）：强制回底并恢复跟随（B5d）。"""
        self.jump_to_bottom()

    def _is_at_bottom(self) -> bool:
        sb = self.verticalScrollBar()
        return sb.value() >= sb.maximum() - (self.fontMetrics().height() // 2)

    def _check_paging(self, text: str) -> None:
        if not _PAGE_RE.search(text):
            return
        if self._page_count >= _MAX_AUTO_PAGES:
            self._auto_paging = False
            self.autopage_hit.emit(self._page_count)
            return
        self._page_count += 1
        self.autopage_hit.emit(self._page_count)

    # ---------- 滚屏（B5d） ----------
    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        if self._is_at_top():
            if not self._view_all:
                self._load_earlier()
        if self._is_at_bottom():
            if self._following is False and self._new_since_pause:
                self._new_since_pause = 0
                self.new_btn.setVisible(False)
                self.jump_to_bottom()
            self._following = True
            # 回到底部恢复跟随后：一次性裁剪到窗口上限（回看期间跳过的 trim）
            self._trim_history()
        else:
            self._following = False

    def _is_at_top(self) -> bool:
        sb = self.verticalScrollBar()
        return sb.value() <= 0

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_End and (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.jump_to_bottom()
            return
        super().keyPressEvent(e)

    def jump_to_bottom(self) -> None:
        self._following = True
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        self._new_since_pause = 0
        self.new_btn.setVisible(False)

    def _update_new_btn(self) -> None:
        self.new_btn.setText(f"新消息 {self._new_since_pause}")
        self.new_btn.adjustSize()
        vp = self.viewport()
        self.new_btn.move(vp.width() - self.new_btn.width() - 12,
                          vp.height() - self.new_btn.height() - 12)
        self.new_btn.setVisible(self._new_since_pause > 0)
        self.new_btn.raise_()

    # ---------- 搜索 ----------
    def highlight(self, text: str, limit: int = 1000) -> int:
        """扫描全文收集命中位置，一次 setExtraSelections 上色；返回命中数。

        大量命中时仅保留前 limit 个，防止超大 ExtraSelection 引发崩溃。
        B5：同时按行汇总命中（每行最多保留第一个命中），经 search_hits_updated 给分屏。
        """
        self._search_matches: list[tuple[int, int]] = []
        self._search_index = -1
        self.setExtraSelections([])
        if text:
            cur = QTextCursor(self.document())
            cur.movePosition(QTextCursor.MoveOperation.Start)
            count = 0
            while count < limit:
                cur = self.document().find(text, cur)
                if cur.isNull():
                    break
                self._search_matches.append((cur.selectionStart(), cur.selectionEnd()))
                count += 1
            self._draw_search()
        self._emit_search_hits(text)
        return len(self._search_matches)

    def _emit_search_hits(self, text: str) -> None:
        """按行汇总命中 → 分屏列表 [(1基行号, 行全文)]。"""
        hits: list[tuple[int, int]] = []
        doc = self.document()
        if text:
            for idx in range(doc.blockCount()):
                blk = doc.findBlockByNumber(idx)
                line = blk.text()
                if text in line:
                    hits.append((idx + 1, line))
        self.search_hits_updated.emit(hits)

    def _draw_search(self) -> None:
        from PyQt6.QtWidgets import QTextEdit

        base = QColor("#3a4a66")
        current = QColor("#b34700")
        extras = []
        n = len(self._search_matches)
        for i, (a, b) in enumerate(self._search_matches):
            c = QTextCursor(self.document())
            c.setPosition(a)
            c.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = c
            sel.format.setBackground(current if i == self._search_index else base)
            sel.format.setForeground(QColor("#ffffff"))
            extras.append(sel)
        self.setExtraSelections(extras)

    def match_count(self) -> int:
        return len(self._search_matches)

    def current_match(self) -> int:
        return self._search_index

    def go_to_match(self, delta: int) -> int:
        """向上/向下跳转结果；当前命中用橙色单独高亮。返回当前序号(-1 表示无命中)。"""
        n = len(self._search_matches)
        if not n:
            return -1
        self._search_index = (self._search_index + delta) % n
        self._draw_search()
        start, end = self._search_matches[self._search_index]
        cur = QTextCursor(self.document())
        cur.setPosition(start)
        cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cur)
        self.ensureCursorVisible()
        return self._search_index

    def find_next(self) -> int:
        """E8-快捷键：F3 查找下一个命中。无搜索词或已清空时返回 -1。"""
        if not getattr(self, "_search_matches", None):
            return -1
        return self.go_to_match(1)

    def go_to_line(self, block_no: int) -> None:
        """B5 搜索分屏：跳转并定位到指定行号(1基)。"""
        doc = self.document()
        idx = max(0, min(block_no - 1, doc.blockCount() - 1))
        blk = doc.findBlockByNumber(idx)
        cur = QTextCursor(blk)
        cur.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        self.setTextCursor(cur)
        self.centerCursor()
        self._following = False

    def clear(self) -> None:
        """清屏：清空可见文档，历史保留（B5 _history 不动）。"""
        self._search_matches = []
        self._search_index = -1
        self.setExtraSelections([])
        super().clear()

    def clear_history(self) -> None:
        """清空历史：同时清空可见文档与 _history。"""
        self.clear()
        if getattr(self, "_history", None) is not None:
            self._history.clear()

    # ---------- 右键菜单 ----------
    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("复制", self.copy)
        menu.addAction("添加到记事本", self._notepad_add)
        menu.addSeparator()
        raw = self.textCursor().selectedText()
        sel = re.sub(r"[\u2029\u2028\n\r\t]+", " ", raw).strip()
        menu.addAction("填写命令", lambda: self.command_fill_requested.emit(sel))
        menu.addAction("发送命令", lambda: self.command_send_requested.emit(sel))
        menu.addAction("看", lambda: self.look_send_requested.emit(sel))
        menu.addAction("NPC对话", lambda: self.ask_fill_requested.emit(sel))
        menu.addSeparator()
        menu.addAction("搜索…", lambda: self.search_requested.emit(raw))
        menu.addAction("新建触发器…", lambda: self.new_trigger_requested.emit(sel))
        menu.addAction("添加到屏显屏蔽", lambda: self.screen_block_add_requested.emit(sel))
        menu.addSeparator()
        menu.addAction("字体设置…", self._open_font_dialog)
        menu.addAction("清屏", self.clear)
        menu.exec(event.globalPos())

    def copy_text_selected(self) -> None:
        self.copy()

    def _notepad_add(self) -> None:
        """右键「添加到记事本」：把选中文本（保留富文本格式）发出给记事本面板。"""
        cur = self.textCursor()
        if cur.hasSelection():
            frag = QTextDocumentFragment(cur)
            self.notepad_add_requested.emit(frag)

    def _open_font_dialog(self) -> None:
        dlg = FontDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.set_font_spec(dlg.result_spec())

    def paused(self) -> bool:
        return not self._following


class FontDialog(QDialog):
    """字体设置对话框（B5/B7）：等宽字体 + 字号。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("字体设置")
        cur = cfg.ConfigManager.instance().get("font", {"family": "Consolas", "size": 12})
        combo = QFontComboBox(self)
        combo.addItems(["Consolas", "NSimSun", "新宋体", "Courier New", "SimHei",
                        "DejaVu Sans Mono", "微软雅黑"])
        combo.setCurrentFont(QFont(cur.get("family", "Consolas"), int(cur.get("size", 12))))
        spin = QSpinBox(self)
        spin.setRange(6, 48)
        spin.setValue(int(cur.get("size", 12)))
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(combo)
        lay.addWidget(spin)
        lay.addWidget(box)
        self._combo, self._spin = combo, spin

    def result_spec(self) -> dict:
        return {"family": self._combo.currentFont().family(), "size": self._spin.value()}