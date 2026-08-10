# 脚本 API 细化（B8 Lua 细化）

> 基于 B8 Lua 已有定稿，细化执行模型与 API 绑定，做到后台可控、能力齐全、可调试，避免后期返工。

## 一、执行模型：后台可控线程
- 默认**后台工作线程**执行，主线程（UI/网络）不阻塞。
- 支持**暂停 / 恢复 / 停止**（用户随时 Stop，安全退出）。
- 短操作（如几行发命令）仍可同步走主线程，但默认为后台。
- 生命周期：
  保持脚本对象 → `run()` 入队到工作线程 → 期间发 `on_log / on_done / on_paused / on_resumed` 信号 → `stop()` 请求取消 → `_stop_requested` 标志被脚本首条语句检查。

（沿用旧版 `LuaRuntimePool` 工作线程+信号模型，但按下面 API 重写，不照抄）

## 二、脚本可访问能力（全部，分命名空间注入）
统一把能力以 Lua table/全局注入运行时：
| 命名空间 | 能力 |
|---|---|
| `send(cmd)` | 发送命令（回车）；`sendRaw`/多命令 batch（延时可选）|
| `trigger` | 注册/删/启停触发器（动态）|
| `timer` | 建/停定时器 |
| `macro` | 运行/停止宏 |
| `bus` | 事件订阅/发布（频道、状态、look.parsed 等）|
| `var` | 存取变量池（全局/账号作用域；**仅运行期内存，不落盘**，见 B3）|
| `state` | 读人物状态（hp/mp/经验/位置/技能快照）|
| `sleep(sec)` | 暂停指定秒（线程内可中断）|
| `out` | 输出到日志/脚本输出窗口（debug/info/warn/error）|
| `sys` | 系统信息、获取客户端状态 |
| `nav` | walk / walkStep / stop（复用地图导航）|

> 与 B3c MUD DSL 的 `sys/*/my` 命名空间保持对应，同一个底层能力提供 Lua 与 DSL 两个入口，不双份维护。

## 三、数据源注入
- 传递 context：`run(name, code, context)` 可把「当前房间/当前账号/当前频道」等快照注入脚本，与 D 多账号兼容（每账号脚本上下文独立）。
- 服务端来源的 API（如地图 route/搜索）也通过命名空间暴露，脚本可调。

## 四、调试支持
| 能力 | 实现 |
|---|---|
| 测试对话框 | 独立沙箱 run，test 界面显示日志 |
| 日志输出 | `print/out` 打到脚本日志窗口，多行滚动 |
| 错误定位 | 捕获异常 → 显示文件:行号 + 堆栈 + 变量帧 |
| **断点调试** | 预留（后端挂 lua 断点，UI 后续版本）|
| 并发安全 | 脚本执行可中断，timeout 兜底防死循环（可设超时）|

## 五、安全与隔离
- 每脚本独立运行实例；互不干扰。
- 超时兜底（默认如 60s 可选）自动停；防死循环卡线程。
- 脚本异常不崩溃客户端，用 on_error 上报 UI。

## 六、文件与接口
- `scripting/lua_runtime.py`（运行时池/后台线程）
- `scripting/script_engine.py`（脚本定义/CRUD/运行/错误回调）
- `scripting/bindings/`（命名空间绑定：#send/timer/alias/event/var/out/skill_check）
- 持久化 `%APPDATA%\XkxClient\config\scripts.json`，可 import 文件。

## 七、验收
- [ ] 后台线程执行，可暂停/恢复/停止，不卡 UI
- [ ] 全部能力命名空间可用（send/自动化/事件/变量/状态/日志/nav）
- [ ] 与 DSL 共享变量与能力，不重复实现
- [ ] 调试：日志、错误行号、测试盒、超时兜底
- [ ] 多账号上下文隔离
- [ ] JSON 脚本 CRUD + import