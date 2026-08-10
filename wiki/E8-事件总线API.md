# 事件总线 API（基础件其一，供自动化/脚本/UI 共用）

> 单一事件源：网络、GMCP、自动化、状态、look、地图 等模块产生的变化统一发布到总线，
> 各订阅方（UI、触发器、定时器、宏、脚本、MUD DSL）订阅关注事件。全客户端唯一总线实例。

## 一、能力
| 能力 | 说明 |
|---|---|
| pub/sub + 优先级 | `subscribe(event, cb, priority)`；同事件按优先级降序执行；`unsubscribe` 可移除 |
| 线程安全 | 由任意线程（后台 worker / UI）发布；Green 队列已在 `Core/Bus`（QObject）内，队列切换保证主线程消费 |
| 通配符订阅 | 支持 `state.*`、`GMCP.*` 形式订阅一组事件 |
| 事件日志 | 记录事件名+参数到调试日志，方便排查 |
| 取消订阅 | 按 (event, callback) 或 token 移除 |

## 二、API
```python
class EventBus(QObject):
    # 发布
    def publish(event: str, **kwargs): ...        # kwargs 为事件负载
    # 订阅
    def subscribe(event: str, cb, priority: int = 0) -> subscription
    def unsubscribe(event: str, cb=None, sub=None): ...
    def clear(): ...
    # 通配符
    def subscribe_pattern(event_glob: str, cb): ...   # "state.*"
    # 日志/调试
    def set_verbose(bool), on_event(cb) ...
```

## 三、预定义事件源（一次定义全，命名约定 `<模块>.<动作>`）
### 网络
- `net.connecting` / `net.connected` / `net.disconnected`（带原因）
- `net.text_raw`（原始一行，未解析）/ `net.text_display`（已解析待显示）

### GMCP（事件名统一大写，与抓包原始模块名一致）
- `GMCP.Status`（状态数据）
- `GMCP.Move`（移动，含 result+dir+short）
- `GMCP.Chat` / `GMCP.System` / `GMCP.Message`
- `GMCP.Combat`（订阅但未触发时给空，备用）

### 自动化
- `trigger.fired(name)` / `trigger.disabled` / `trigger.paused` 状态变更
- `timer.fired` / `timer.started` / `timer.stopped`
- `macro.started` / `macro.state`（步/暂停/完成/停止）/ `macro.stopped`
- `script.started` / `script.paused` / `script.resumed` / `script.stopped` / `script.error`

### 状态
- `state.changed`（hp/mp/经验/位置变化，带字段）
- `state.room`（当前房间变化——由 GMCP.Move 归并后发；承载位置更新供 UI/地图/脚本用）

### look 解析
- `look.parsed`（携带 LookResult，供房间详情/实体搜索/变量池）

### 地图
- `map.pushed` / `map.cache_refreshed` / `map.error`

### 输入/UI
- `input.sent`（用户发了命令）/ `input.focus`
- `ui.message`（通知/日志入口，供脚本 out 与托盘通知统一）

## 四、脚本 / MUD DSL 共用接口
- 事件总线统一暴露给：
  - Lua: `bus.subscribe("state.*", fn)` / `event.emit("my.ev", {...})`
  - MUD DSL (B3c): 同一命名空间 `bus.sub/in pub`，与 Lua 同事件源。
- 共用同一 Bus 实例即可复用事件名/日志，不双份。

## 五、多账号作用域（配合 D4）
- 采用**全局唯一总线**模型：客户端仅有一个 EventBus 实例。
- 多账号隔离靠事件负载的 `account` 字段：订阅方声明关注某 `account` 时才接收（默认全部接收，事件负载里带 `account` 供判断）。
- 自动化引擎实例订阅时按自己的账号过滤，避免多账号互相影响；事件名相同、负载带 `account` 即可区分作用到哪个账号。

## 六、验收
- [ ] pub/sub + 优先级 + 取消 + 通配符 + 线程安全
- [ ] 预定义事件一次到位（网络/GMCP/自动化/状态/look/地图/输入）
- [ ] Lua 与 DSL 用它同一接口
- [ ] 多账号作用域隔离、不串扰
- [ ] 事件日志可开可关