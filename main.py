import sys


def main() -> int:
    from PyQt6.QtWidgets import QApplication

    from xkxclient.app import XkxApp
    from xkxclient.core import resources
    from xkxclient.ui.theme import apply as apply_theme
    from xkxclient.ui.login import LoginWindow
    from xkxclient.ui.mainwindow import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("EasyBXb")
    app.setOrganizationName("EasyBXb")
    apply_theme()

    if (icon := resources.app_icon()) is not None:
        app.setWindowIcon(icon)

    core = XkxApp()
    main_window = MainWindow(core)
    login = LoginWindow(core, main_window)
    login.show()

    code = app.exec()
    core.shutdown()
    return code


if __name__ == "__main__":
    raise SystemExit(main())