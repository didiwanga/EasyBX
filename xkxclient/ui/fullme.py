from __future__ import annotations

import re

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from xkxclient.core.fullme import extract_fullme_url  # noqa: F401 (re-export for UI)

_IMG_RE = re.compile(rb"<img[^>]+src\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)


class FullmeGridWindow(QDialog):
    """E-fullme 验证码窗口：2×2 网格同时显示 4 个验证码（4 次 fullme 各一张）。

    无 QtWebEngine 时降级：HTTP 下载图片（QLabel）显示；若地址是 HTML 页则解析
    其中的 <img src> 再下载真正的图片。
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

        self.input_row = QLineEdit()
        self.input_row.setPlaceholderText("输入验证码后回车")
        self.input_row.returnPressed.connect(self._send)
        send_btn = QPushButton("发送")
        send_btn.clicked.connect(self._send)

        lay = QVBoxLayout(self)
        lay.addStretch(0)
        lay.addLayout(grid)
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
        from PyQt6.QtCore import QTimer

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
        self.close()


class FullmeWindow(FullmeGridWindow):
    """单次 fullme 弹窗（兼容旧逻辑）：单图 + 按来源布局。"""

    def __init__(self, session, source: str = "manual", url: str = "", parent=None) -> None:
        self._legacy_source = source
        super().__init__(session, urls=[url] if url else [], parent=parent)
        self.setWindowTitle("fullme 验证码")
        if self._legacy_source != "manual" and not url:
            pass
            # task 来源且无链接时不显示输入行（避免误发）