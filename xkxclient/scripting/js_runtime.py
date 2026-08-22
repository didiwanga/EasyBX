from __future__ import annotations

import asyncio
import json
import threading
import time
import traceback
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from xkxclient.headless.control import QtBridge as _QtMainBridge

# JS 脚本宿主基于 mini-racer（V8）。依赖缺失时 load() 友好报错，不影响其他功能。
try:
    from py_mini_racer import MiniRacer
    _HAS_MR = True
except ImportError:  # pragma: no cover
    MiniRacer = None  # type: ignore
    _HAS_MR = False

_BOOTSTRAP = r"""
// ---- EasyBXb JS 脚本宿主引导层 ----
var __actions = [];      // {id, re, pat, fn, once}
var __alias_fns = {};    // 函数型别名（my.alias 注册）
var __retry_watch = {};  // sys.retry 等待表
var __seq = 0;

function __remove_action(id){ __actions = __actions.filter(a => a.id !== id); }

function sleep(ms){ return new Promise(r => setTimeout(r, Math.max(0, ms))); }

function __match_line(a, raw){
  try {
    if (a.re) return raw.match(a.re);
    if (raw.indexOf(a.pat) >= 0) return [a.pat];
  } catch (e) { L('[匹配错误] ' + e); }
  return null;
}

// 主线程每行调用：先重试等待表，再用户动作表
function __host_on_line(raw, clean){
  for (const id in __retry_watch){
    const w = __retry_watch[id];
    let m = null;
    try { m = w.re ? raw.match(w.re) : (raw.indexOf(w.pat) >= 0 ? [w.pat] : null); }
    catch (e) {}
    if (m){
      clearTimeout(w.t0);
      delete __retry_watch[id];
      w.resolve(true);
    }
  }
  for (const a of __actions.slice()){
    const m = __match_line(a, raw);
    if (!m) continue;
    if (a.once) __remove_action(a.id);
    try {
      const r = a.fn(m);
      if (r && typeof r.catch === 'function')
        r.catch(e => L('[动作错误] ' + (e && e.message ? e.message : e)));
    } catch (e) {
      L('[动作错误] ' + (e && e.message ? e.message : e));
    }
  }
}

async function __dispatch(p){ return __py_bridge(p); }

function __run_alias_by_name(name, arg){
  return __dispatch({op: 'run_alias', name: String(name ?? ''), arg: arg == null ? '' : String(arg)});
}

const my = {};
const sys = {};
const com = { vars: {} };

my.alias = function(tbl){
  for (const k in tbl){
    const v = tbl[k];
    if (typeof v === 'function'){
      __alias_fns[k] = v;
      __dispatch({op: 'alias_fn', name: k});
    } else {
      delete __alias_fns[k];
      __dispatch({op: 'alias_str', name: k, cmd: String(v ?? '')});
    }
  }
};

sys.action = function(id, pat, fn, once){
  id = String(id);
  __remove_action(id);
  const re = (pat instanceof RegExp) ? pat : null;
  __actions.push({id, re, pat: re ? null : String(pat), fn, once: !!once});
};
sys.unaction = function(id){ __remove_action(String(id)); };
my.action = function(pat, fn){ sys.action('$on' + (++__seq), pat, fn, false); };

sys.send = function(cmd){ return __dispatch({op: 'send', cmd: String(cmd ?? '')}); };
sys.send_raw = function(cmd){ return __dispatch({op: 'send_raw', cmd: String(cmd ?? '')}); };

function L(msg){ __dispatch({op: 'out', level: 'info', msg: String(msg ?? '')}); }
function LOG(msg){ __dispatch({op: 'out', level: 'log', msg: String(msg ?? '')}); }
sys.info = L; sys.log = LOG;
const console2 = { log: LOG, info: L, error: LOG, warn: LOG };

sys.sleep = function(sec){ return sleep(Math.max(0, (Number(sec) || 0) * 1000)); };
sys.retry = async function(aliasName, successPat, retries, intervalSec){
  const re = (successPat instanceof RegExp) ? successPat : new RegExp(String(successPat));
  const iv = Math.max(1, Number(intervalSec) || 5) * 1000;
  const n = Math.max(1, Number(retries) || 1);
  for (let i = 0; i < n; i++){
    const hit = await new Promise(resolve => {
      const id = '$rt' + (++__seq);
      const t0 = setTimeout(() => {
        if (__retry_watch[id]){ delete __retry_watch[id]; resolve(false); }
      }, iv);
      __retry_watch[id] = {re, resolve, t0};
      __run_alias_by_name(aliasName);
    });
    if (hit) return true;
  }
  return false;
};

// 走路：path 为 `;` 分隔命令串，逐条经别名/变量管道发送
sys.xy = function(loc, path){
  return __dispatch({op: 'nav_xy', loc: String(loc ?? ''), path: String(path ?? '')});
};
my.map = {
  xy: function(target, cmd){
    return __dispatch({op: 'map_xy', target: String(target ?? ''), cmd: String(cmd ?? '')});
  }
};
com.gpsback = function(name){ return __run_alias_by_name(name); };

sys.state = async function(){ const r = await __dispatch({op: 'state'}); return r || {}; };

// 兼容 console（V8 裸上下文无内置 console）
var console = console2;
"""


class JsScriptHost(QObject):
    """持久型 JS 脚本宿主（mini-racer/V8）。

    - 加载即执行脚本顶层（注册别名/触发器），事件驱动回调，直到 unload。
    - 所有引擎触碰经 _MainBridge 序列化到 Qt 主线程；JS 一律跑在 V8 内部
      asyncio 循环上；两侧互不阻塞（入口 fire-and-forget，回调带超时等待）。
    - 兼容 pz-new.js 类脚本的宿主 API：my.alias/my.action/sys.action/
      sys.unaction/sys.send/sys.info/sys.log/sys.sleep/sys.retry/sys.xy/
      my.map.xy/com.vars/com.gpsback/console。
    """

    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, session, name: str, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.name = name
        self._mr = None
        self._loop = None
        self._bridge = _QtMainBridge()
        self._pump = QTimer(self)
        self._pump.setInterval(30)
        self._pump.timeout.connect(self._bridge.pump)
        self._bus_sub = None
        self._dyn_names: list[str] = []
        self._loaded = False
        self._stopping = False
        self._stop_evt: asyncio.Event | None = None
        self._done_emitted = False

    # ---- 生命周期 ----
    @property
    def running(self) -> bool:
        return self._loaded and not self._stopping

    def load(self, code: str) -> bool:
        if not _HAS_MR:
            self.log.emit("未安装 mini-racer（pip install mini-racer），无法运行 JS 脚本")
            self.finished.emit(False, "missing mini-racer")
            return False
        try:
            self._mr = MiniRacer()
            self._mr.set_hard_memory_limit(512 * 1024 * 1024)
            self._loop = self._mr._ctx.event_loop
        except Exception as exc:
            self.log.emit(f"V8 初始化失败: {exc}")
            self.finished.emit(False, str(exc))
            return False

        ready = threading.Event()

        async def host_main():
            self._stop_evt = asyncio.Event()
            async with self._mr.wrap_py_function(self._dispatch) as fn:
                inject = self._mr.eval("(f) => { globalThis.__py_bridge = f; }")
                inject(fn)
                # 在 mr 循环内不能用带 timeout 的 eval（会断言失败）；
                # 用 eval_cancelable + wait_for 做顶层执行超时保护
                await asyncio.wait_for(
                    self._mr.eval_cancelable(BOOTSTRAP_SRC), timeout=15)
                await asyncio.wait_for(
                    self._mr.eval_cancelable(code), timeout=15)
                self._loaded = True
                ready.set()
                await self._stop_evt.wait()

        def on_host_done(fut):
            try:
                fut.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                ready.set()  # 让 load() 不用等满超时
                if not self._stopping and not self._done_emitted:
                    self._done_emitted = True
                    tb = traceback.format_exc(limit=6)
                    self.log.emit(f"[JS 异常] {exc}")
                    self.finished.emit(False, f"{exc}\n{tb}")
                return
            if self._stopping and not self._done_emitted:
                self._done_emitted = True
                self.finished.emit(True, "")

        fut = asyncio.run_coroutine_threadsafe(host_main(), self._loop)
        fut.add_done_callback(on_host_done)
        # 泵必须立刻启动：脚本顶层就会调 my.alias/my.action，依赖桥回主线程执行；
        # 主线程在此等待循环里周期 processEvents 驱动 QTimer 泵与信号投递
        self._pump.start()
        from PyQt6.QtCore import QCoreApplication
        deadline = time.monotonic() + 15
        while not ready.is_set() and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            ready.wait(0.02)
        if not ready.is_set():
            msg = "脚本加载超时（顶层执行超过 15 秒）"
            self.log.emit(msg)
            self.unload()
            if not self._done_emitted:
                self._done_emitted = True
                self.finished.emit(False, msg)
            return False
        if not self._loaded:
            # 顶层代码抛异常：on_host_done 已发 finished(False)
            return False

        self._bus_sub = self.session.app.bus.subscribe(
            "net.text_display", self._on_text)
        self.log.emit(f"JS 脚本「{self.name}」已加载")
        return True

    def unload(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._pump.stop()
        if self._bus_sub is not None:
            try:
                self.session.app.bus.unsubscribe(
                    "net.text_display", sub=self._bus_sub)
            except Exception:
                pass
            self._bus_sub = None
        eng = getattr(self.session, "aliases", None)
        for n in self._dyn_names:
            try:
                eng.remove_dynamic(n)
            except Exception:
                pass
        self._dyn_names.clear()

        async def _stop():
            if self._stop_evt is not None:
                self._stop_evt.set()

        if self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(_stop(), self._loop).result(5)
            except Exception:
                pass
        try:
            self._mr.close()
        except Exception:
            pass
        self._loaded = False

    # ---- JS 入口（主线程 → V8，fire-and-forget）----
    def _schedule_js(self, expr: str) -> None:
        async def run():
            try:
                await self._mr.eval_cancelable(expr)
            except Exception as exc:
                self.log.emit(f"[JS] {exc}")
        if self._loaded and self._loop is not None:
            asyncio.run_coroutine_threadsafe(run(), self._loop)

    def _eval(self, code: str, timeout_ms: int = 10000):
        """同步求值（仅主线程上下文使用）。timeout 单位毫秒。"""
        return self._mr.eval(code)

    # ---- 总线 → JS ----
    def _on_text(self, payload: dict) -> None:
        if payload.get("account") != self.session.account_id:
            return
        clean = str(payload.get("line") or "")
        segs = payload.get("segments") or []
        parts: list[str] = []
        prev: tuple | None = None
        for sgm in segs:
            style = (sgm.get("fg"), sgm.get("bg"), bool(sgm.get("bold")),
                     bool(sgm.get("blink")))
            esc = ""
            if style != prev:
                codes = []
                if style[3]:
                    codes.append("5")
                if style[2]:
                    codes.append("1")
                fg, bg = style[0], style[1]
                codes.append((str(fg) if fg else "39"))
                codes.append((str(bg) if bg else "49"))
                esc = "\x1b[" + ";".join(codes) + "m"
                prev = style
            parts.append(esc + str(sgm.get("t") or ""))
        raw = "".join(parts)
        self._schedule_js("__host_on_line(%s, %s)"
                          % (json.dumps(raw), json.dumps(clean)))

    # ---- V8 → 主线程分发（跑在 mr loop 协程里）----
    def _dispatch(self, payload: dict) -> dict | None:
        op = str(payload.get("op") or "")
        ev = threading.Event()
        res: dict = {}

        def on_main():
            try:
                out = self._handle(op, payload)
                res["result"] = out
            except Exception as exc:
                res["error"] = f"{exc}"
            finally:
                ev.set()

        self._bridge.post(on_main)
        if not ev.wait(timeout=10):
            return {"error": "main-thread timeout"}
        if "error" in res:
            raise RuntimeError(res["error"])
        return res.get("result")

    def _handle(self, op: str, p: dict):
        s = self.session
        if op == "send":
            cmd = str(p.get("cmd") or "").strip()
            if cmd:
                s.send(cmd)
            return {"ok": True}
        if op == "send_raw":
            cmd = str(p.get("cmd") or "").strip()
            if cmd:
                s.connection.send_line(cmd)
            return {"ok": True}
        if op == "out":
            self.log.emit("[{}] {}".format(p.get("level"), p.get("msg")))
            return None
        if op == "alias_str":
            from xkxclient.automation.alias import Alias
            name = str(p.get("name") or "")
            s.aliases.register_dynamic(Alias(
                name=name, pattern=name, replacement=str(p.get("cmd") or "")))
            self._remember(name)
            return {"ok": True}
        if op == "alias_fn":
            from xkxclient.automation.alias import Alias
            name = str(p.get("name") or "")

            def handler(text, m, _n=name):
                arg = text
                self._schedule_js(
                    "__run_user_alias(%s, %s)" % (json.dumps(_n), json.dumps(arg)))
            s.aliases.register_dynamic(Alias(
                name=name, pattern=name, replacement="", handler=handler))
            self._remember(name)
            return {"ok": True}
        if op == "run_alias":
            name = str(p.get("name") or "")
            arg = str(p.get("arg") or "")
            a = s.aliases.find_dynamic(name)
            if a is not None and a.handler is not None:
                a.handler(arg, None)
                return {"ok": True}
            # 字符串别名或普通配置别名：直接按替换串发送
            expanded = s.aliases.expand(arg or name)
            if expanded:
                s.send(expanded)
                return {"ok": True}
            s.send(arg or name)
            return {"ok": True}
        if op == "nav_xy":
            path = str(p.get("path") or "")
            steps = [x.strip() for x in path.split(";") if x.strip()]
            self._send_sequence(steps)
            return {"ok": True, "steps": len(steps)}
        if op == "map_xy":
            target = str(p.get("target") or "")
            after = [x.strip() for x in str(p.get("cmd") or "").split(";")
                     if x.strip()]
            cache = getattr(s, "map_cache", None)
            route = cache.route(target) if cache is not None else None
            steps = list(route or [])
            self._send_sequence(steps + after)
            return {"ok": True, "routed": bool(route), "steps": len(steps)}
        if op == "state":
            st = s.state
            return {"account": s.account_id, "name": st.name,
                    "room": s.room_name, "exits": list(s.exits),
                    "connected": bool(s.connected),
                    "logged_in": bool(s.logged_in)}
        return {"error": f"unknown op {op}"}

    def _remember(self, name: str) -> None:
        if name not in self._dyn_names:
            self._dyn_names.append(name)

    def _send_sequence(self, steps: list[str], interval_ms: int = 300) -> None:
        """主线程：逐条发送命令串（QTimer 链），供走路/回程用。"""
        if not steps:
            return

        def step(i: int) -> None:
            if self._stopping or i >= len(steps):
                return
            try:
                self.session.send(steps[i])
            except Exception:
                traceback.print_exc()
                return
            QTimer.singleShot(interval_ms, lambda: step(i + 1))

        step(0)


BOOTSTRAP_SRC = _BOOTSTRAP + """
globalThis.__run_user_alias = async function(name, arg){
  const fn = __alias_fns[name];
  if (typeof fn !== 'function') return;
  try {
    const r = fn(arg);
    if (r && typeof r.catch === 'function')
      r.catch(e => L('[别名错误] ' + (e && e.message ? e.message : e)));
  } catch (e) {
    L('[别名错误] ' + (e && e.message ? e.message : e));
  }
};
"""
