from __future__ import annotations

import fnmatch
from collections import defaultdict
from typing import Any, Callable

from PyQt6.QtCore import QObject, pyqtSignal


class EventBus(QObject):
    """全局唯一事件总线（wiki E8-事件总线API.md）。

    - pub/sub + 优先级：同事件按优先级降序执行
    - 线程安全：publish 可从任意线程调用，回调经 Qt 队列切回主线程消费
    - 通配符：支持 ``state.*`` 订阅一组事件
    - 回调签名：``cb(payload: dict)``，负载含 ``event`` 字段
    """

    _dispatch = pyqtSignal(str, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._subs: dict[str, list[tuple[int, Callable]]] = defaultdict(list)
        self._patterns: dict[str, list[tuple[int, Callable]]] = defaultdict(list)
        self._verbose = False
        self._dispatch.connect(self._run)

    # ---- 发布（任意线程）----
    def publish(self, event: str, **kwargs: Any) -> None:
        payload: dict[str, Any] = {"event": event, **kwargs}
        payload.setdefault("account", None)
        self._dispatch.emit(event, payload)

    # ---- 订阅 ----
    def subscribe(self, event: str, cb: Callable, priority: int = 0):
        self._subs[event].append((priority, cb))
        return cb

    def subscribe_pattern(self, event_glob: str, cb: Callable, priority: int = 0):
        self._patterns[event_glob].append((priority, cb))
        return cb

    def unsubscribe(self, event: str, cb: Callable | None = None, sub: Callable | None = None) -> None:
        target = cb or sub
        if event not in self._subs:
            return
        self._subs[event] = [(p, c) for p, c in self._subs[event] if c is not target]
        if not self._subs[event]:
            del self._subs[event]

    def unsubscribe_pattern(self, event_glob: str, cb: Callable) -> None:
        if event_glob not in self._patterns:
            return
        self._patterns[event_glob] = [(p, c) for p, c in self._patterns[event_glob] if c is not cb]
        if not self._patterns[event_glob]:
            del self._patterns[event_glob]

    def clear(self) -> None:
        self._subs.clear()
        self._patterns.clear()

    # ---- 调试 ----
    def set_verbose(self, enabled: bool) -> None:
        self._verbose = enabled

    # ---- 主线程消费 ----
    def _run(self, event: str, payload: dict) -> None:
        for _pri, cb in sorted(self._subs.get(event, []), key=lambda x: x[0], reverse=True):
            try:
                cb(payload)
            except Exception:
                import sys
                import traceback
                traceback.print_exc(file=sys.stderr)
        for pattern in list(self._patterns):
            if fnmatch.fnmatch(event, pattern):
                for _pri, cb in sorted(self._patterns[pattern], key=lambda x: x[0], reverse=True):
                    try:
                        cb(payload)
                    except Exception:
                        import sys
                        import traceback
                        traceback.print_exc(file=sys.stderr)