from __future__ import annotations

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon, QMessageBox

from xkxclient.core import resources
from xkxclient.ui.about import VERSION, AboutDialog


class AppTray(QSystemTrayIcon):
    """系统托盘（E / R）：图标 + 恢复/隐藏菜单。"""

    def __init__(self, main_window, parent=None) -> None:
        super().__init__(parent)
        self.main = main_window
        icon = resources.app_icon()
        if icon is not None:
            self.setIcon(icon)
        self.setToolTip(f"EasyBXb {VERSION}")
        menu = QMenu()
        act_show = QAction("显示主窗口", menu)
        act_show.triggered.connect(self._restore)
        act_about = QAction("关于", menu)
        act_about.triggered.connect(lambda: AboutDialog(main_window).exec())
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_show)
        menu.addAction(act_about)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _restore(self) -> None:
        self.main.showNormal()
        self.main.raise_()
        self.main.activateWindow()

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._restore()

    def _quit(self) -> None:
        # 与窗口关闭一致：弹「正在关闭」进度窗（shutdown 会阻塞最多 3s 优雅登出）
        if hasattr(self.main, "_show_shutdown_progress"):
            self.main._show_shutdown_progress()
        else:
            self.main.app.shutdown()
        self.main.close()

    def notify(self, title: str, message: str) -> None:
        if self.isVisible():
            self.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information)
        else:
            QMessageBox.information(None, title, message)