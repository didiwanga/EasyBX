from __future__ import annotations

import re

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from xkxclient.core.fullme import extract_fullme_url  # noqa: F401 (re-export for UI)

_IMG_RE = re.compile(rb"<img[^>]+src\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)

# 描述行：`本次fullme请输入<描述>的字`（显示在输入框上方）
_DESC_RE = re.compile(r"本次fullme请输入(.+?)的字")

# 服务器回话：成功 / 失败
_SUCCESS_MSG = "你突然感到精神一振，浑身似乎又充满了力量！"
_FAIL_MSG = "好像什么都没有发生，但是又好像有什么事情做错了。再来一次试试！"

# 录入次数上限：1 次首发 + 2 次错误重输
_MAX_ATTEMPTS = 3
# 发送验证码后超时无任何回话（成功/失败均未收到）→ 按失败处理，提示重输
_RESULT_TIMEOUT_MS = 180000


class FullmeGridWindow(QDialog):
    """E-fullme 验证码窗口：2×2 网格同时显示 4 个验证码（4 次 fullme 各一张）。

    无 QtWebEngine 时降级：HTTP 下载图片（QLabel）显示；若地址是 HTML 页则解析
    其中的 <img src> 再下载真正的图片。

    发送验证码后（FullmeWindow/FullmeGridWindow）：不立即关闭，等待服务器回话——
    - 成功 `你突然感到精神一振…`：自动关闭窗口；
    - 失败 `好像什么都没有发生…`：提示「输入的验证码可能有误」，清空等待重输，
      最多 3 次（1 次首发 + 2 次错误重输），用尽则关闭；
    - 超时无回话按失败处理。
    宏验证码窗口（CaptchaWindow）不启用该模式，提交即关窗。
    """

    def __init__(self, session, urls: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        # 服务器每次 fullme 只给 1 个链接，且该链接可被打开/刷新 4 次（3 分钟后失效）。
        # 因此取第一个有效链接，在 4 个格子各请求一次（内容相同，方便对图判断）。
        self._url = ""
        for u in (urls or []):
            if u:
                self._url = str(u)
                break
        self.setWindowTitle("fullme 验证码 2×2（同一链接×4）" if self._url else "fullme 验证码")
        self.setModal(False)
        self.setMinimumSize(460, 300)

        grid = QGridLayout()
        labels: list[QLabel] = []
        for idx in range(4):
            cell = QLabel("加载中…")
            cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.setMinimumSize(200, 120)
            cell.setFrameShape(QLabel.Shape.StyledPanel)
            if self._url:
                cell.setProperty("url", self._url)
            else:
                cell.setText("无验证码")
            grid.addWidget(cell, idx // 2, idx % 2)
            labels.append(cell)
        self._labels = labels

        self.desc_label = QLabel()
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet("color:#9fd0ff; padding:2px 4px;")
        self.desc_label.hide()

        self.input_row = QLineEdit()
        self.input_row.setPlaceholderText("输入验证码后回车")
        self.input_row.returnPressed.connect(self._send)
        send_btn = QPushButton("发送")
        send_btn.clicked.connect(self._send)

        lay = QVBoxLayout(self)
        lay.addStretch(0)
        lay.addLayout(grid)
        lay.addWidget(self.desc_label)
        lay.addWidget(self.input_row)
        lay.addWidget(send_btn)

        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self._nam = QNetworkAccessManager(self)
        self._replies: list[QNetworkReply] = []
        self._label_urls: dict[QLabel, str] = {}
        for label in self._labels:
            url = label.property("url")
            if url:
                self._label_urls[label] = str(url)
                self._load_into(self._nam, str(url), label)

        # 等待结果模式：发送后不立即关窗，监听服务器回话（宏验证码窗口除外）
        self._attempts = 0
        self._result_timer: QTimer | None = None
        self._sub = None
        if self.wait_result_enabled():
            self._sub = self.session.app.bus.subscribe("net.text_display", self._on_text)

    def wait_result_enabled(self) -> bool:
        """是否在发送后等待服务器回话（自动关闭/重输）。默认 True；宏验证码窗口覆盖为 False。"""
        return True

    # ---- 服务器回话监听 ----
    def _on_text(self, payload: dict) -> None:
        if (payload.get("account") or "") != self.session.account_id:
            return
        text = payload.get("line") or ""
        if not text:
            return
        m = _DESC_RE.search(text)
        if m:
            desc = m.group(1).strip()
            self.desc_label.setText(f"本次fullme请输入{desc}的字")
            self.desc_label.show()
            return
        if _FAIL_MSG in text:
            self._clear_result_timer()
            if self._attempts >= _MAX_ATTEMPTS:
                self._do_close()
                return
            QMessageBox.information(self, "验证码错误",
                                    "输入的验证码可能有误，请仔细辨别")
            self.input_row.clear()
            self.input_row.setFocus()
            return
        if _SUCCESS_MSG in text:
            self._clear_result_timer()
            self._do_close()

    def _do_close(self) -> None:
        self._clear_result_timer()
        self.close()

    def _clear_result_timer(self) -> None:
        if self._result_timer is not None:
            self._result_timer.stop()
            self._result_timer = None

    def _load_into(self, nam, url: str, label: QLabel) -> None:
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent", b"Mozilla/5.0 (compatible; EasyBXb)")
        # 同一链接独立打开 4 次：禁用缓存，确保每次都是真实重新请求
        from PyQt6.QtNetwork import QNetworkRequest as _NR
        req.setAttribute(_NR.Attribute.CacheLoadControlAttribute,
                         _NR.CacheLoadControl.AlwaysNetwork)
        reply = nam.get(req)
        self._replies.append(reply)  # 持有引用，防止 reply 被 GC 导致信号不触发
        reply.finished.connect(lambda: self._on_reply(reply, label))

        # 兜底：10s 未完成显示超时，避免一直停在「加载中…」
        def timeout():
            if not reply.isFinished():
                reply.abort()
                label.setText("加载超时")

        QTimer.singleShot(10000, timeout)

    def _on_reply(self, reply: QNetworkReply, label: QLabel) -> None:
        url = label.property("url") or ""
        if reply.error() != QNetworkReply.NetworkError.NoError:
            label.setText(f"加载失败\n{reply.errorString()}")
            return
        data = bytes(reply.readAll())
        ct = reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader)
        # 调试：记录响应类型/大小，帮助定位「无法解析图片」
        if getattr(self, "_debug_log", None) is not None:
            try:
                head = data[:80]
                self._debug_log(f"[fullme] {url} type={ct} size={len(data)} head={head!r}")
            except Exception:
                pass
        m = _IMG_RE.search(data) if (data[:1] == b"<" or b"<" in data[:64]) else None
        if m:
            src = m.group(1).decode("utf-8", "replace").strip()
            child = QUrl(str(url)).resolved(QUrl(src))
            self._load_into(self._nam, child.toString(), label)
            return
        pix = QPixmap()
        if pix.loadFromData(data):
            label.setPixmap(pix.scaled(label.width() if label.width() > 100 else 220,
                                       160, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation))
        else:
            label.setText(f"无法解析图片\n{url}")

    def _send(self) -> None:
        code = self.input_row.text().strip()
        if not code:
            return
        self.session.send(f"fullme {code}")
        self.input_row.clear()
        if not self.wait_result_enabled():
            self.close()
            return
        # 等待结果模式：不关闭，等到成功/失败/超时回话
        self._attempts += 1
        if self._attempts >= _MAX_ATTEMPTS:
            self._do_close()
            return
        # 计时兜底：超时按失败处理
        self._clear_result_timer()
        self._result_timer = QTimer(self)
        self._result_timer.setSingleShot(True)
        self._result_timer.timeout.connect(self._on_timeout)
        self._result_timer.start(_RESULT_TIMEOUT_MS)

    def _on_timeout(self) -> None:
        self._result_timer = None
        if self._attempts >= _MAX_ATTEMPTS:
            self._do_close()
            return
        QMessageBox.information(self, "验证码错误",
                                "输入的验证码可能有误，请仔细辨别")
        self.input_row.clear()
        self.input_row.setFocus()

    def closeEvent(self, event) -> None:
        if self._sub is not None:
            try:
                self.session.app.bus.unsubscribe("net.text_display", self._sub)
            except Exception:
                pass
            self._sub = None
        self._clear_result_timer()
        super().closeEvent(event)


class FullmeWindow(FullmeGridWindow):
    """单次 fullme 弹窗（兼容旧逻辑）：单图 + 按来源布局。"""

    def __init__(self, session, source: str = "manual", url: str = "", parent=None) -> None:
        self._legacy_source = source
        super().__init__(session, urls=[url] if url else [], parent=parent)
        self.setWindowTitle("fullme 验证码")
        if self._legacy_source != "manual" and not url:
            pass
            # task 来源且无链接时不显示输入行（避免误发）


class CaptchaWindow(FullmeGridWindow):
    """宏「验证码」步骤弹窗：布局同 fullme，但提交时不发 `fullme` 命令，
    而是把用户输入回传给回调（宏引擎据此赋值变量并继续下一步）。

    不启用「等待服务器回话」模式：提交即关窗。
    """

    def __init__(self, session, url: str = "", on_submit=None, parent=None) -> None:
        self._on_submit = on_submit
        super().__init__(session, urls=[url] if url else [], parent=parent)
        self.setWindowTitle("宏验证码")

    def wait_result_enabled(self) -> bool:
        return False

    def _send(self) -> None:
        code = self.input_row.text().strip()
        if not code:
            return
        self.input_row.clear()
        if self._on_submit is not None:
            self._on_submit(code)
        self.close()