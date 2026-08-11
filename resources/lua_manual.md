# EasyBXb Lua 脚本完整手册

> 适用版本：客户端 0.1.0（`sys.info()` 可查）。北大侠客行（PKUXKX）专用。
> 运行时：`lupa`（Lua 5.x 兼容语法）。脚本在独立后台线程执行，永不阻塞界面。
> 打开方式：菜单「脚本 → Lua 脚本…」（工具栏 📜 脚本）→ 弹出的脚本编辑器。

---

## 1. 脚本编辑器与脚本库

### 1.1 入口
- 顶部菜单 **脚本 → Lua 脚本…**，或工具栏 **📜 脚本**按钮，或命令面板 `script_edit`。
- 帮助菜单「Lua 脚本手册」可随时打开本手册全文（含本用例搜索）。

### 1.2 编辑器功能
| 功能 | 说明 |
|---|---|
| 脚本列表 | 左侧列出全部脚本（`%APPDATA%\XkxClient/scripts.json` 持久化） |
| 新建/保存 | 顶部按钮；保存需输入脚本名称 |
| 运行 / 停止 | 选中脚本点「运行」在当前账号会话后台跑；运行中可「暂停/继续」「停止」 |
| 导入 / 导出 | `.lua` 文件与 `lua/` 目录互转 |
| 运行日志 | 右侧面板显示 `out()` 输出与错误（含文件/行号/堆栈） |
| 超时兜底 | 默认 **60 秒** 自动置中止，编辑器可调；脚本内四处 `sleep/tick` 处安全退出 |

> 未安装 `lupa` 时点运行会友好报错提示安装，不会崩溃。

### 1.3 运行模型（重要）
- 每个脚本 = 独立后台线程 + 独立 Lua 运行时，同名重复运行自动顶掉旧实例。
- `bus` 事件是唯一跨模块入口：**主线程发事件 → 脚本线程等待队列 → `sleep()`/`bus.poll()` 时派发回调**；回调内可正常 `send/out/sleep`。
- 变量 `var` 与触发器 / MUD DSL 共用同一账号会话变量池（仅运行期内存）。
- 多账号互不共享 `var`、`state`；事件负载带 `account` 字段可区分。

---

## 2. 全局函数

### 2.1 `send(cmd)`
发送命令（回车）。多行用 `\n` 分开；走完整发送管线（可带 `;` 拆分、写历史、限速、别名/DSL 前置）。
```lua
send("hp")
send("go north\ngo south")   -- 两条命令
```

### 2.2 `sendRaw(text)`
原文发送一行（直接 `send_line`，不过别名/DSL/命令拆分）。多行用 `\n` 分开。
```lua
sendRaw("say 测试")
sendRaw("ask cui about 新手任务")   -- 含空格原样发出，避免被拆分
```

### 2.3 `sleep(seconds)`
阻塞等待。可被「暂停/继续/停止」打断：被打断抛 Lua 错误（脚本停止）；等待期间派发已订阅的 bus 事件。
```lua
sleep(2.5)
```

### 2.4 `out(text, level?)`
输出到脚本运行日志。level：`info`（默认）/`debug`/`warn`/`error`/`ok` 等。
```lua
out("开始挂机")
out("危险！", "warn")
```

---

## 3. 命名空间表（`trigger` / `timer` / `macro` / `bus` / `var` / `state` / `nav` / `sys`）

### 3.1 `trigger` — 动态触发器
| 函数 | 签名 | 返回值 | 说明 |
|---|---|---|---|
| `register` | `(name, pattern, action, opts?)` | `bool` | 注册；同名自动覆盖旧触发器 |
| `remove` | `(name)` | — | 移除 |
| `enable` | `(name)` | — | 启用 |
| `disable` | `(name)` | — | 停用（不再匹配） |
| `count` | `(name)` | `int` | 命中次数 |

**`opts`（可选表）**：
| 键 | 默认 | 说明 |
|---|---|---|
| `match_type` | `"contains"` | `contains` = 行内包含；`template` = 模板捕获 `{变量}`，命中写入 `var` |
| `delay_ms` | `0` | 命中后延迟执行动作（毫秒） |
| `one_shot` | `false` | 只触发一次 |
| `group` | `""` | 分组名 |

**`action` 三种写法**：
```lua
-- 1) 字符串命令（可多条用 ; 或 \n 分隔）
trigger.register("t1", "你得到了", "out('获得物品')")
-- 2) 命令列表
trigger.register("t2", "【闲聊】", {"say hi", "score"})
-- 3) 动作表（与自动化工具栏同格式）
trigger.register("t3", "你受到攻击", {type="cmd", command="yun recover"})
```

**模板捕获示例**：
```lua
trigger.register("hp", "你的气血：{qi} / {max}", "out('气血 '..var.get('qi'))",
                 {match_type="template", one_shot=false})
```

### 3.2 `timer` — 定时器
| 函数 | 签名 | 返回值 | 说明 |
|---|---|---|---|
| `after` | `(ms, action, name?)` | `string`（名字） | 每 `ms` 毫秒循环执行一次（间隔定时器） |
| `stop` | `(name)` | — | 停止指定 |
| `stopAll` | `()` | — | 停止全部 |
| `list` | `()` | `list[string]` | 全部定时器名 |

```lua
local id = timer.after(30000, "save", "auto_save")
timer.stop("auto_save")
```
> 说明：`after` 是循环定时器；一次性请在动作里自行 `timer.stop`。`name` 缺省自动生成。

### 3.3 `macro` — 宏
| 函数 | 签名 | 返回值 | 说明 |
|---|---|---|---|
| `run` | `(name)` | `bool` | 运行宏（返回是否成功启动） |
| `stop` | `()` | — | 停止全部 |
| `pause` | `()` | — | 暂停 |
| `resume` | `()` | — | 继续 |
| `list` | `()` | `list[string]` | 已定义宏名列表 |

```lua
macro.run("挂机打坐")
```

### 3.4 `bus` — 事件总线
| 函数 | 签名 | 返回值 | 说明 |
|---|---|---|---|
| `subscribe` | `(topic, callback)` | `bool` | 订阅；`callback(payload)`。回调在 `sleep/ poll` 时执行 |
| `unsubscribe` | `(topic)` | — | 退订本脚本该主题全部回调 |
| `publish` | `(topic, data?)` | — | 发布（自动带 `account` 与 `data`） |
| `poll` | `(topic?)` | `int` | 手动清空待处理事件并回调；空串=全部。返回处理条数 |

```lua
bus.subscribe("GMCP.Status", function(p)
  out("气血：", p.data and p.data.qi)
end)
```
> payload 是表：至少含 `event`（主题名）、`account`（账号id）；各主题另有自己的字段（见 §4）。
> 脚本停止时自动退订，不用手动清理。

### 3.5 `var` — 变量池（与触发器 / DSL 共享）
| 函数 | 签名 | 说明 |
|---|---|---|
| `set` | `(k, v)` | 存值（字符串/数字/布尔） |
| `get` | `(k, default?)` | 取值，缺省返回 default |
| `unset` | `(k)` | 删除 |
| `all` | `()` | 返回全量表 |

```lua
var.set("target", "wudang")
if var.get("hp", 0) < 100 then send("eat liang") end
```
> 只是内存，不持久化。

### 3.6 `state` — 人物状态（只读）
| 函数 | 签名 | 说明 |
|---|---|---|
| `get` | `(key, default?)` | 单项状态值 |
| `all` | `()` | 全部标量状态表 |

常用键：`qi / max_qi / eff_qi`，`jing / max_jing / eff_jing`，`jingli / max_jingli`，
`neili / max_neili`，`food / water / fighter_spirit`，`busy / fighting`，
`level / combat_exp / potential`，`name / id / title / family`，
`str / dex / int / con / per / vigour / yuan`。
```lua
if state.get("qi", 0) < state.get("max_qi", 1) / 2 then send("exert recover") end
```
> 数据来源：GMCP.Status 推送 + 内部状态机；未推送前取默认值。

### 3.7 `nav` — 地图导航（含节点查询）
| 函数 | 签名 | 说明 |
|---|---|---|
| `walk` | `(dirs)` | 按方向序列自动行走；`dirs` 可为表 `{"north","east"}` 或空格分隔字符串 |
| `stop` | `()` | 停止导航 |
| `stepMs` | `(ms)` | 设置每步步进间隔毫秒（默认约 1500） |
| `currentRoom` | `()` | 当前房间名（MapCache.current） |
| `route` | `(target)` | 本地 BFS 到目标的**方向序列**；`nil`=无路 |
| `roomExits` | `(room)` | 某节点房间的出口列表 |
| `rooms` | `()` | 当前账号已知全部节点（房间名）排序列表 |

```lua
nav.walk({"north", "east", "south"})
nav.walk("north east south")
nav.stepMs(1200)
nav.stop()
local r = nav.route("比武场")
if r and #r > 0 then nav.walk(r) end
out("已知道路 " .. #nav.rooms() .. " 个，当前 " .. nav.currentRoom())
```
> 走法特点：每步等 `GMCP.Move` 确认，`result=false`（撞墙）立即停；连续超时也停。
> 导航进度以事件发布在总线上（见 §4 map.*）。

### 3.8 `sys` — 系统信息
| 函数 | 签名 | 说明 |
|---|---|---|
| `info` | `()` | 表：`client/version/account/connected/logged_in/room/exits` |
| `name` | `()` | 当前账号 id |
| `room` | `()` | 当前房间名 |
| `exits` | `()` | 当前出口列表 |
| `tick` | `()` | 让出检查点：忙循环请调用，便于「停止」打断（会抛错终止） |

```lua
local i = sys.info()
out(i.client .. " " .. i.version .. " @ " .. i.account)
```

---

## 4. 客户端事件总线（可订阅主题全集）

> 订阅回调里可 sleep/发命令/输出。事件名统一命名约定 `模块.动作`，GMCP 主题与抓包模块名一致。
> **注意**：回调不会在纯计算循环中被触发——请用 `while true do bus.poll(); sleep(0.1) end` 或让 `sleep()` 派发。

### 4.1 网络
| 主题 | 载荷字段 | 说明 |
|---|---|---|
| `net.connecting` | `account, status, attempt` | 自动重连进度 |
| `net.connected` | `account, host` | 已连接 |
| `net.disconnected` | `account, reason` | 断开（带原因） |
| `net.text_display` | `account, line` | 每行文本显示前（未分流） |

### 4.2 GMCP（北大侠客行服务端推送，见 §5 字段详解）
| 主题 | 载荷 | 说明 |
|---|---|---|
| `GMCP.Status` | `data` | 人物状态（自动订阅） |
| `GMCP.Move` | `data` | 移动确认（自动订阅；`result/short/dir`） |
| `GMCP.System` | `data` | 系统信息（自动订阅） |
| `GMCP.Message` | `data` | QQ 群消息等（自动订阅） |
| `GMCP.Combat` | `data` | 战斗数据（已订阅；低等级/非战斗不推送） |
| `GMCP.Buff` | `data` | 状态 buff（同上，按服务器实际） |

> 订阅方式在登录后自动 `tune gmcp <频道> on`；低等级 Combat/Buff 无数据不代表模块不可用。

### 4.3 状态 / look / 地图 / UI / 自动化
| 主题 | 载荷 | 说明 |
|---|---|---|
| `state.changed` | `state`（全量状态表） | 状态条目变化（气血/内力/位置等） |
| `state.room` | `account, name, exits` | 当前房间变化（GMCP.Move 归并 / look 解析后发布） |
| `look.parsed` | `result` | look 解析完成；`result.room/entities/status/raw`（见下方） |
| `map.pushed` / `map.cache_refreshed` / `map.error` | `account` 等 | 地图/导航数据变化 |
| `ui.message` | `account, message` | 客户端提示消息 |
| `input.sent` / `input.focus` | — | 用户输入命令 / 焦点 |
| `trigger.fired` / `timer.fired` / `macro.started` 等 | — | 自动化状态变更（E8 定义） |
| `script.started/paused/resumed/stopped/error` | — | 脚本生命周期 |
| `login.done` | `account` | 自动登录完成 |

### 4.4 `look.parsed` 载荷结构
`result` 为解析表：
```lua
-- result.room: { name, category, exits = { "north", "east", ... }, desc = { ... } }
-- result.entities: { { name, desc }, ... }  房间内 NPC/物品
-- result.status:  { ... }                   该行状态文本（若有）
-- result.raw:     { ... }                   原始行
bus.subscribe("look.parsed", function(p)
  local r = p.result
  if r and r.room then
    out("房间：" .. r.room.name .. " 出口：" .. table.concat(r.room.exits, ","))
  end
end)
```

---

## 5. 服务器（PKUXKX）GMCP 推送字段参考

### 5.1 `GMCP.Status` — 人物状态（最常用）
服务端推送 `module=Status.Status`（JSON 对象），客户端解析为 `data`：
| 字段 | 类型 | 含义 |
|---|---|---|
| `name` `id` `title` `family` | string | 中文名 / id / 头衔 / 门派 |
| `level` | number | 等级 |
| `combat_exp` `potential` | string(数值) | 实战经验 / 潜能 |
| `qi` `max_qi` `eff_qi` | number | 气血 / 上限 / 有效气血 |
| `jing` `max_jing` `eff_jing` | number | 精神 / 上限 / 有效精神 |
| `jingli` `max_jingli` | number | 精力 / 上限 |
| `neili` `max_neili` | number | 内力 / 上限 |
| `food` `water` | number | 饭 / 水（饥饿口渴度） |
| `fighter_spirit` | number | 战意 |
| `is_busy` `is_fighting` | string `"true"/"false"` | 忙 / 战斗（同时写 state.busy/fighting） |
| `str` `dex` `int` `con` `per` | number | 膂力 / 身法 / 悟性 / 根骨 / 容貌 |

同时触发 `state.changed`、`state.room`；中文名/门派/级别会回写账号配置。

### 5.2 `GMCP.Move` — 移动确认
服务端推送 `module=Move.Move`，载荷为**数组**（每元素是一步）或对象：
```json
[{"result": true, "short": "岳阳城广场", "dir": ["north","east"]}]
```
| 字段 | 含义 |
|---|---|
| `result` | `true/false`（字符串 `"true"/"1"` 也按真处理）是否移动成功 |
| `short` | 移动后房间短名（自动更新 room_name / sys.room()） |
| `dir` | 当前房间出口方向数组 |
客户端兼容两种格式并同步到 `state.room`。

### 5.3 `GMCP.System` / `GMCP.Message` / `GMCP.Combat` / `GMCP.Buff`
- 字节流原样走 `gmcp.parse_payload`；`data` 为该模块内容（可能为 list / dict / str）。
- `GMCP.System`：服务器系统消息。`GMCP.Message`：如 QQ 群消息（channel="QQ"，type "pic"/"text"，seq、no 等）。
- `GMCP.Combat`/`GMCP.Buff`：仅服务器主动推送后才有数据；订阅由客户端自动完成（`send("tune gmcp Combat on")` 亦可手动）。

### 5.4 手动控制订阅
```lua
send("tune gmcp Status on")
send("tune gmcp Combat on")
```

---

## 6. 常用插件式脚本示例

### 6.1 自动补血回内（靠 GMCP.Status 推送）
```lua
bus.subscribe("GMCP.Status", function(p)
  local d = p.data
  if not d then return end
  local qi, mq = tonumber(d.qi), tonumber(d.max_qi)
  if qi and mq and qi < mq * 0.5 then send("exert recover") end
end)
while true do bus.poll(); sleep(0.5) end
```

### 6.2 循环吃干粮喝水（定时器）
```lua
timer.after(60000, function()
  if state.get("food", 300) < 100 then send("eat liang") end
  if state.get("water", 300) < 100 then send("drink jiudai") end
end, "eat_drink")
```

### 6.3 自动 look + 房间巡视
```lua
bus.subscribe("state.room", function(p)
  out("到达 " .. tostring(p.name) .. " 出口=" .. table.concat(p.exits or {}, ","))
  send("look")
end)
while true do bus.poll(); sleep(0.2) end
```

### 6.4 启动即导航到目标
```lua
bus.subscribe("login.done", function()
  out("登录完成，前往比武场")
  nav.walk("north east east south")
end)
while true do bus.poll(); sleep(1) end
```

### 6.5 导航 dock（客户端内置「导航目的地」）
菜单 **查看 → 导航目的地** 打开。功能：
- **实时节点**：每次移动/ look 立即替换显示 当前房间名、类别、描述、NPC、出口按钮（出口点一下=走一步）。
- **目的地一键走**：输入或双击收藏目的地 → 本地 BFS（不足走服务端 route）→ 自动全程 walk。
- **收藏**：`收藏` 按钮把当前房间保存到配置 `map.favorites`；`移除` 取消。
- **walk 状态**：`nav.*` 事件实时回显 行走中(剩N步)/已到达/卡住/停止。
- 同一客户端脚本可用 `nav.rooms()/route()/currentRoom()` 复刻该 dock 的目的地列表与寻路。

---

## 7. 避坑与提示
1. **纯计算死循环**不会被自动打断：请写 `while true do sleep(0.1) end`，或忙循环里调用 `sys.tick()`。
2. **stop 打断点**只出现在 `sleep()`/`sys.tick()` 处；长耗时不 sleep 的脚本，停止会等待回到这些检查点。
3. `bus` 回调**只在 `sleep()` 或 `bus.poll()` 时执行**——千万别写只订阅不同步的脚本。
4. 多账号登录时各账号独立运行脚本；同一账号下 Lua/触发器/DSL 共享 `var`。
5. `trigger/timer/macro` 的 action **只支持** 字符串命令/命令列表/动作表，不支持 Lua 函数引用。
6. 事件负载里有 `account` 字段，多账号订阅时用它做过滤，避免跨号误触发。
7. GMCP/状态是异步推送：`send("hp")` 后不会立刻出现 `state.qi`，需等数据到达。
8. 服务器低等级可能不推 `combat_exp` 等字段：数值转 `tonumber` 前先判空。