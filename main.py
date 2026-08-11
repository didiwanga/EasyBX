import os
import sys


def _self_test() -> int:
    """EASYX_SELFTEST=1 时的冻结运行时自检：装配 + Lua 执行，结果写临时日志。

    仅服务于打包验证/回归，不参与正常启动。
    """
    import tempfile
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from PyQt6.QtCore import QTimer

    from xkxclient.app import XkxApp

    app = QApplication(sys.argv)
    core = XkxApp()
    sess = core.session("selftest")
    mg = core.scripts()
    mg.save("st1", 'local v = 40 + 2\nout("SUM=" .. v)\nvar.set("k2", v)\nsend("hp")\n',
            timeout=10, enabled=False)
    collected = []
    result = {}

    def _finish(ok: bool, det: str) -> None:
        result["ok"] = ok
        result["det"] = det
        result["k2"] = sess.vars.get("k2")

    runner = mg.run(sess, "st1")
    if runner is None:
        result["ok"] = False
        result["det"] = "runner None"
        result["k2"] = None
    else:
        runner.log.connect(collected.append)
        runner.finished.connect(_finish)

    start = time.time()

    def _poll() -> None:
        if "ok" in result:
            _write()
            app.quit()
        elif time.time() - start > 12:
            result["ok"] = False
            result["det"] = "selftest timeout"
            _write()
            app.quit()

    def _write() -> None:
        try:
            out_path = os.environ.get("EASYX_SELFTEST_OUT") or os.path.join(
                tempfile.gettempdir(), "xkx_selftest.log")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("ok=%s\n" % result.get("ok"))
                f.write("k2=%r\n" % result.get("k2"))
                f.write("det=%r\n" % (result.get("det") or "")[:400])
                f.write("logs=%r\n" % collected)
        except OSError:
            pass

    QTimer.singleShot(200, _poll)
    app.exec()
    return 0 if result.get("ok") else 1


def main() -> int:
    if os.environ.get("EASYX_SELFTEST"):
        try:
            code = _self_test()
        except Exception as exc:  # 任何异常都要能回读，避免黑盒
            import traceback

            out_path = os.environ.get("EASYX_SELFTEST_OUT") or os.path.join(
                __import__("tempfile").gettempdir(), "xkx_selftest.log")
            try:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write("ok=False\n")
                    f.write("boom=%s\n" % traceback.format_exc())
            except OSError:
                pass
            return 2
        raise SystemExit(code)

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