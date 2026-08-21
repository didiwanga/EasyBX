# -*- coding: utf-8 -*-
"""北侠查询小程序：内嵌浏览器打开 Quick BI 看板，两个标签页（NPC 查询 / 房间查询）。

独立小程序，不依赖 xkxclient 包，打包后仅含 PyQt6 + QtWebEngine。
"""

import sys

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QToolBar,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

PAGES = [
    ("NPC 查询",
     "https://bi.aliyuncs.com/token3rd/dashboard/view/pc.htm?pageId="
     "f672ef03-84cb-40ea-a4c1-a8c09a7bee78"
     "&accessToken=9025b10058ca7ac74c24365dc94bbcbf&dd_orientation=auto"),
    ("房间查询",
     "https://bi.aliyuncs.com/token3rd/dashboard/view/pc.htm?pageId="
     "650455df-9217-4971-aaea-008a4ba238f3"
     "&accessToken=7357517e608bf34c041a201bfaa12ec9"
     "&dd_orientation=auto&componentId=7ceth55o"),
]


class QueryWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("北侠查询")
        self.resize(1280, 820)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.views: list[QWebEngineView] = []
        for name, url in PAGES:
            view = QWebEngineView(self)
            view.load(QUrl(url))
            self.tabs.addTab(view, name)
            self.views.append(view)
        self.tabs.currentChanged.connect(self._sync_actions)
        self._current = self.views[0]

        tb = QToolBar("导航", self)
        tb.setMovable(False)
        self.addToolBar(tb)
        self.act_back = QAction("← 后退", self)
        self.act_fwd = QAction("→ 前进", self)
        self.act_refresh = QAction("↻ 刷新", self)
        self.act_home = QAction("🏠 主页", self)
        self.act_back.triggered.connect(lambda: self._current.back())
        self.act_fwd.triggered.connect(lambda: self._current.forward())
        self.act_refresh.triggered.connect(lambda: self._current.reload())
        self.act_home.triggered.connect(lambda: self._current.load(
            QUrl(self._current.url() if self._current.url().isValid() else PAGES[self.tabs.currentIndex()][1])))
        for a in (self.act_back, self.act_fwd, self.act_refresh, self.act_home):
            tb.addAction(a)
        self._sync_actions()

    def _current_view(self) -> QWebEngineView:
        i = self.tabs.currentIndex()
        self._current = self.views[i]
        return self._current

    def _sync_actions(self) -> None:
        self._current_view()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("北侠查询")
    win = QueryWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
