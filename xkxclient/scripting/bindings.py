from __future__ import annotations

import time

from xkxclient.automation.timer import TimerDef
from xkxclient.automation.trigger import Trigger
from xkxclient.version import VERSION

CLIENT_NAME = "EasyBXb"
CLIENT_VERSION = VERSION


def _opt(opts, key: str, default=None):
    """从 Python dict 或 lupa table（无 .get 的 FusenObject）安全取选项。"""
    if opts is None:
        return default
    if isinstance(opts, dict):
        return opts.get(key, default)
    if isinstance(opts, (list, tuple)):
        return opts[int(key)] if key.isdigit() and int(key) < len(opts) else default
    sentinel = object()
    try:
        v = opts.get(key, sentinel)
        if v is sentinel:
            return default
        return v
    except (AttributeError, TypeError):
        pass
    try:
        return opts[key]
    except Exception:
        return default


def _coerce_list(value, split: bool = True) -> list:
    """把 lupa 传入的 Lua 表 / Python 可迭代 / 字符串 统一转成 list。"""
    if value is None:
        return []
    if isinstance(value, str):
        if not split:
            return [value]
        return [p.strip() for p in value.replace(";", " ").split() if p.strip()]
    if hasattr(value, "keys"):  # lua table / 类 dict
        try:
            keys = list(value.keys())
            ordered = list(value)
            if keys == ordered:
                return [value[k] for k in keys]
            return list(value)
        except Exception:
            return list(value)
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _to_actions(action) -> list[dict]:
    """把脚本层 action（字符串 / 命令列表 / 动作 dict）转成 ActionRunner 动作表。"""
    if isinstance(action, dict):
        return [dict(action)]
    if isinstance(action, (list, tuple)):
        cmds = " ; ".join(str(c) for c in action)
        return [{"type": "cmd", "command": cmds}] if cmds.strip() else []
    if isinstance(action, str):
        return [{"type": "cmd", "command": action}] if action.strip() else []
    raise ValueError("action 仅支持字符串命令 / 命令列表 / 动作表")


class LuaBindings:
    """E8 脚本 API：给一次 Lua 运行注入全部能力命名空间。

    命名空间：send / sendRaw / sleep / out / trigger / timer / macro /
    bus / var / state / nav / sys。与 B3c 的 sys.*/DSL 共享底层能力。
    除 sleep 外全部在主线程入口回调；lupa 回调经 Qt 队列切回工作线程执行。
    """

    def __init__(self, session, worker) -> None:
        self._s = session
        self._w = worker

    def build(self) -> dict:
        b: dict = {
            "send": self._send,
            "sendRaw": self._send_raw,
            "sleep": self._w.sleep,
            "out": self._out,
            "trigger": self._trigger_table(),
            "timer": self._timer_table(),
            "macro": self._macro_table(),
            "bus": self._bus_table(),
            "var": self._var_table(),
            "state": self._state_table(),
            "nav": self._nav_table(),
            "sys": self._sys_table(),
        }
        return b

    # ---- 基础 ----
    def _send(self, cmd) -> None:
        for line in _coerce_list(str(cmd), split=False):
            line = line.strip()
            if line:
                self._s.send(line)

    def _send_raw(self, cmd) -> None:
        for line in _coerce_list(str(cmd), split=False):
            line = line.strip()
            if line:
                self._s.connection.send_line(line)

    def _out(self, text, level: str = "info") -> None:
        self._w.log.emit("[%s] %s" % (str(level), text))

    # ---- trigger ----
    def _trigger_table(self) -> dict:
        s = self._s

        def register(name: str, pattern: str, action, opts: dict | None = None) -> bool:
            t = Trigger(
                name=str(name),
                match_type=str(_opt(opts, "match_type", "contains")),
                pattern=str(pattern),
                actions=_to_actions(action),
                delay_ms=int(_opt(opts, "delay_ms", 0) or 0),
                enabled=True,
                one_shot=bool(_opt(opts, "one_shot", False)),
                group=str(_opt(opts, "group", "")),
            )
            s.triggers.triggers[:] = [x for x in s.triggers.triggers if x.name != t.name]
            s.triggers.triggers.append(t)
            self._w.log.emit("[trigger] 注册 %s" % t.name)
            return True

        def remove(name: str) -> None:
            s.triggers.triggers[:] = [x for x in s.triggers.triggers if x.name != str(name)]

        def enable(name: str) -> None:
            s.triggers.enable(str(name))

        def disable(name: str) -> None:
            s.triggers.disable(str(name), stop=False)

        return {"register": register, "remove": remove,
                "enable": enable, "disable": disable,
                "count": s.triggers.count}

    # ---- timer ----
    def _timer_table(self) -> dict:
        s = self._s
        _seq = [0]

        def after(ms, action, name: str | None = None) -> str:
            name = name or ("$lua%d_%d" % (time.time(), _seq[0]))
            _seq[0] += 1
            td = TimerDef(name=str(name), enabled=True,
                          schedule={"type": "interval", "interval_ms": max(1, int(ms))},
                          actions=_to_actions(action))
            s.timers.timers[name] = td
            s.timers.start(name)
            self._w.log.emit("[timer] 建立 %s (%sms)" % (name, int(ms)))
            return name

        def stop(name: str) -> None:
            s.timers.stop(str(name))

        def stop_all() -> None:
            s.timers.stop_all()

        def names() -> list[str]:
            return list(s.timers.timers.keys())

        return {"after": after, "stop": stop, "stopAll": stop_all, "list": names}

    # ---- macro ----
    def _macro_table(self) -> dict:
        s = self._s
        return {"run": s.macros.start, "stop": s.macros.stop,
                "pause": s.macros.pause, "resume": s.macros.resume,
                "list": s.macros.list}

    # ---- bus ----
    def _bus_table(self) -> dict:
        s = self._s
        w = self._w

        def subscribe(topic: str, callback=None) -> bool:
            if callback is None:
                return False
            w._bus_cbs.setdefault(str(topic), []).append(callback)

            def handler(payload: dict) -> None:
                w._enqueue(str(topic), payload)

            w._sub_handlers.append((str(topic), handler))
            s.app.bus.subscribe(str(topic), handler)
            return True

        def unsubscribe(topic: str) -> None:
            w._bus_cbs.pop(str(topic), None)

        def publish(topic: str, data=None) -> None:
            s.app.bus.publish(str(topic), account=s.account_id, data=data)

        def poll(topic: str = "") -> int:
            """取出已订阅 topic 的待处理事件并回调；空串轮询全部。返回处理条数。"""
            return w.bus_poll(str(topic) or None)

        return {"subscribe": subscribe, "unsubscribe": unsubscribe,
                "publish": publish, "poll": poll}

    # ---- var（与 B3/DSL 共用 session.vars，仅内存）----
    def _var_table(self) -> dict:
        s = self._s
        return {"set": lambda k, v: s.vars.__setitem__(k, v),
                "get": lambda k, default=None: s.vars.get(k, default),
                "unset": lambda k: s.vars.pop(k, None),
                "all": lambda: dict(s.vars)}

    # ---- state ----
    def _state_table(self) -> dict:
        s = self._s

        def get(key: str, default=None):
            return getattr(s.state, str(key), default)

        def all_() -> dict:
            return {k: v for k, v in vars(s.state).items()
                    if v is None or isinstance(v, (bool, int, float, str))}

        return {"get": get, "all": all_}

    # ---- nav ----
    def _nav_table(self) -> dict:
        s = self._s
        cache = getattr(s, "map_cache", None)

        def current_room() -> str:
            return getattr(cache, "current", "") if cache is not None else (s.room_name or "")

        def route(target: str) -> list[str] | None:
            """本地 BFS 寻路到目标，返回方向序列；None=无路。"""
            if cache is None:
                return None
            return cache.route(str(target))

        def room_exits(room: str) -> list[str]:
            if cache is None:
                return []
            node = cache.rooms.get(str(room), {})
            return list(node.get("exits", []) or [])

        def rooms() -> list[str]:
            """当前账号已知的全部节点（房间名），供脚本做目的地列表。"""
            if cache is None:
                return []
            return sorted(cache.rooms.keys())

        return {"walk": lambda dirs: s.navigator.start(_coerce_list(dirs)),
                "stop": s.navigator.stop,
                "stepMs": s.navigator.config_step_ms,
                "currentRoom": current_room,
                "route": route,
                "roomExits": room_exits,
                "rooms": rooms}

    # ---- sys ----
    def _sys_table(self) -> dict:
        s = self._s
        w = self._w

        def info() -> dict:
            return {"client": CLIENT_NAME, "version": CLIENT_VERSION,
                    "account": s.account_id,
                    "connected": bool(s.connected),
                    "logged_in": bool(s.logged_in),
                    "room": s.room_name,
                    "exits": list(s.exits)}

        return {"info": info, "tick": w.ticking,
                "name": lambda: s.account_id,
                "room": lambda: s.room_name,
                "exits": lambda: list(s.exits)}