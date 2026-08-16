from __future__ import annotations

import threading
import time
import traceback

from PyQt6.QtCore import QObject, pyqtSignal


class LuaWorker(QObject):
    """E8 后台线程：在工作线程内执行一段 Lua。

    线程模型：纯 Python threading.Thread 执行 lupa（运行时与回调同线程，
    线程安全）。主线程通过直接方法调用 pause()/stop()（Condition 心跳），
    事件（bus 订阅）用「队列 + 轮询」模型：主线程入队，脚本在 sleep() 或
    bus.poll() 时取出并回调，不在工作线程碰 QApplication。

    - sleep() 期间可暂停/恢复/停止（心跳轮询）。
    - 超时兜底：后台看门线程置 abort 标志，sleep/tick 处抛出，防死循环。
    - 纯计算死循环无法被看门打断（execute 不返回），通过 sys.tick() 让出。
    """

    log = pyqtSignal(str)
    done_ok = pyqtSignal()
    done_err = pyqtSignal(str)

    def __init__(self, timeout: float = 60.0) -> None:
        super().__init__()
        self.timeout = float(timeout or 60.0)
        self._stop = False
        self._abort = False
        self._paused = False
        self._cond = threading.Condition()
        self._bus_cbs: dict[str, list] = {}
        self._bus_queue: dict[str, list] = {}
        self._queue_lock = threading.Lock()
        self._sub_handlers: list[tuple[str, object]] = []
        self._bus = None
        self._thread: threading.Thread | None = None
        self._wd_stop = threading.Event()  # 脚本正常结束时置位，通知看门线程退出

    def attach_bus(self, bus) -> None:
        """绑定事件总线（一次运行内用于批量退订）。"""
        self._bus = bus

    # ---- 主线程控制 ----
    def request_stop(self) -> None:
        self._stop = True
        with self._cond:
            self._cond.notify_all()

    def set_paused(self, paused: bool) -> None:
        with self._cond:
            self._paused = bool(paused)
            if not paused:
                self._cond.notify_all()

    def start_thread(self, target) -> None:
        self._thread = threading.Thread(target=target, name="xkx-lua", daemon=True)
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- Lua 侧可中断原语 ----
    def sleep(self, seconds) -> None:
        secs = max(0.0, float(seconds))
        deadline = time.monotonic() + secs
        while not self._stop:
            if self._abort:
                raise RuntimeError("脚本超时中止")
            with self._cond:
                if self._paused:
                    self._cond.wait(0.05)
                    continue
            self._dispatch_bus()
            now = time.monotonic()
            if now >= deadline:
                return
            time.sleep(min(0.05, max(0.0, deadline - now)))
        raise RuntimeError("脚本已停止")

    def ticking(self) -> None:
        """供忙循环主动让出检查：`sys.tick()`。"""
        if self._stop:
            raise RuntimeError("脚本已停止")
        if self._abort:
            raise RuntimeError("脚本超时中止")

    # ---- bus：队列 + 轮询（主线程入队，工作线程取用）----
    @staticmethod
    def _safe_str(v: str) -> str:
        """清理非法 UTF-8 字节（GBK/乱码流进 Lua 回调会崩）：统一转可靠 UTF-8。"""
        if isinstance(v, bytes):
            try:
                return v.decode("utf-8")
            except UnicodeDecodeError:
                return v.decode("gbk", errors="replace")
        try:
            v.encode("utf-8")
            return v
        except UnicodeEncodeError:
            return v.encode("utf-8", errors="replace").decode("utf-8")

    def _sanitize(self, obj):
        """递归清理 dict/list 里的字符串，保证可安全传给 lupa。"""
        if isinstance(obj, dict):
            return {k: self._sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._sanitize(v) for v in obj]
        if isinstance(obj, str):
            return self._safe_str(obj)
        return obj

    def _enqueue(self, topic: str, payload: dict) -> None:
        payload = self._sanitize(payload)
        with self._queue_lock:
            self._bus_queue.setdefault(topic, []).append(payload)

    def bus_poll(self, topic: str | None) -> int:
        """取出已订阅 topic 的待处理事件并回调 Lua 函数；返回处理条数。"""
        if topic is not None:
            return self._dispatch_topic(str(topic))
        n = 0
        with self._queue_lock:
            topics = list(self._bus_queue.keys())
        for t in topics:
            n += self._dispatch_topic(t)
        return n

    def _dispatch_topic(self, topic: str) -> int:
        with self._queue_lock:
            chunk = self._bus_queue.get(topic)
            if chunk:
                self._bus_queue[topic] = []
            else:
                chunk = None
        if not chunk:
            return 0
        cbs = list(self._bus_cbs.get(topic, []))
        n = 0
        for payload in chunk:
            for cb in cbs:
                try:
                    cb(payload)
                    n += 1
                except Exception:
                    self.log.emit("[bus] 回调错误: %s" % traceback.format_exc(limit=3))
        return n

    def _dispatch_bus(self) -> None:
        with self._queue_lock:
            topics = list(self._bus_queue.keys())
        for t in topics:
            self._dispatch_topic(t)

    def _unsubscribe_all(self) -> None:
        if self._bus is None:
            self._sub_handlers = []
            return
        for topic, handler in self._sub_handlers:
            try:
                self._bus.unsubscribe(topic, sub=handler)
            except Exception:
                pass
        self._sub_handlers = []

    # ---- 执行（工作线程入口）----
    def run(self, code: str, bindings: dict) -> None:
        import lupa  # 延迟导入：未安装时脚本运行友好报错

        runtime = lupa.LuaRuntime()
        threading.Thread(target=self._watchdog, name="xkx-lua-wd", daemon=True).start()
        bench = time.time()
        try:
            g = runtime.globals()
            for name, fn in bindings.items():
                g[name] = fn
            self.log.emit("脚本启动")
            runtime.execute(code)
            if self._stop or self._abort:
                self.log.emit("脚本结束（%s）" % ("被停止" if self._stop else "超时中止"))
            else:
                self.log.emit("脚本完成（%.2fs）" % (time.time() - bench))
            self.done_ok.emit()
        except Exception as exc:
            self.log.emit("脚本错误")
            self.done_err.emit(str(exc) + "\n" + traceback.format_exc(limit=8))
        finally:
            self._wd_stop.set()  # 通知看门线程：脚本已结束，无需再等超时
            self._unsubscribe_all()

    def _watchdog(self) -> None:
        if self._wd_stop.wait(self.timeout):
            return  # 脚本已正常结束，看门线程退出
        if not self._stop:
            self._abort = True
            self.log.emit("[超时] 脚本超过 %ss 未结束，已中止" % self.timeout)


class ScriptRunner(QObject):
    """一次脚本运行：纯 Python 工作线程 + 主线程信号。

    信号：
    - log(msg)             脚本日志行
    - finished(ok, detail) 结束（是否成功，错误明细）
    """

    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self._worker: LuaWorker | None = None

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.alive

    def start(self, code: str, timeout: float = 60.0) -> bool:
        if self.running:
            return False
        worker = LuaWorker(timeout)
        from xkxclient.scripting.bindings import LuaBindings

        bindings = LuaBindings(self.session, worker).build()
        worker.attach_bus(self.session.app.bus)
        worker.log.connect(self.log)
        worker.done_ok.connect(lambda: self.finished.emit(True, ""))
        worker.done_err.connect(lambda e: self.finished.emit(False, e))
        self._worker = worker
        worker.start_thread(lambda: worker.run(code, bindings))
        return True

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.request_stop()

    def pause(self) -> None:
        if self._worker is not None:
            self._worker.set_paused(True)

    def resume(self) -> None:
        if self._worker is not None:
            self._worker.set_paused(False)

    def request_data(self, attr: str):
        """供 UI 观察 worker 内部状态（如 _paused），不直接触碰内部。"""
        w = self._worker
        if w is None:
            return None
        return getattr(w, attr, None)

    def dispose(self) -> None:
        self.stop()