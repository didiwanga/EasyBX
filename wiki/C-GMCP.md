# C 模块：GMCP（定稿 v2，按 pkuxkx 实测报文）

> 依据《北大侠客行 GMCP 详解 v1.0》（数据源 gmcp_recv.log 47,931 条实际推送）定稿。
> **注意**：该资料取自旧客户端按当前账号实测；低等级时部分字段未推送不等于将来不推；旧客户端未订阅的模块（Message/Combat/Buff）新客户端可自行订阅。字段映射以此为准，嵌套兼容写法作为兜底保留，但不作为首选。

## C1 协议接入（Telnet 0xC9 子协商）

字节常量：`IAC=0xFF SB=0xFA SE=0xF0 DO=0xFD WILL=0xFB GMCP_OPT=0xC9`
报文结构：`FF FA C9 <module> <0x20> <JSON> FF F0`
5 态 IAC 状态机：0=normal / 1=IAC / 2=IAC+cmd / 3=SB / 4=SB 中遇 IAC

**握手流程（必备，不发服务器永远不推）**：
1. 服务器发 `WILL 0xC9` → 客户端回 `DO 0xC9` + `Core.Hello`。
2. 客户端连接后主动补发一次 `DO 0xC9` + `Core.Hello`（即使服务器没先 WILL，也建立 GMCP 通道）。
3. 登录完成后 ~3s 自动执行 `tune gmcp <频道> on` 订阅（频道名不带 `GMCP.` 前缀）。

**订阅策略（新客户端全开）**：
- 频道：`Status` / `Move` / `System` / `Combat`（必开）；`Message` / `Buff` 可选（见下）。
- Combat 虽当前 0 推送，但新客户端应订阅，等人物等级提升后战斗推送即生效。

## C2 数据解析（GMCPParser）
- 收 IAC SB 数据 → 按 `模块.命令` 切分（`GMCP.模块`）→ JSON 反序列化 → 事件总线 `GMCP.<Module>` 广播（事件名统一大写，与 E8 一致）。
- **JSON 容错（pkux 特有）**：payload 常直接含 ANSI 控制字符（`\u001b[...`），`json.loads` 失败时 fallback：
  1. `re` 清洗 `[\x00-\x08\x0b\x0c\x0e-\x1f]`
  2. `json.loads(cleaned, strict=False)`
- **双重编码**：偶发整个 JSON 被嵌套成 `{"raw": "{...}"}`，需二次解析。
- ANSI 清洗：`name/title/family/family_name/short` 入 state 前用 `_ANSI_RE.sub('', s).strip()`。

## C3 状态面板（可停靠 dock，全量）
形态：菜单「查看」可勾选，默认悬浮 dock，可停靠/拖出，与快捷动作、移动控制并列（各 dock 独立可拖）。
数据流：GMCP.Status → GMCPRouter → state.update_from_gmcp_status(...) → UI 局部刷新（不整屏重绘）。

**当前状态 dock 字段映射**（按 C2 真实根级字段）：
身份：`name`、`id`、`title`(ANSI 后清洗)、`family/family_name`、`level`、`combat_exp`、`potential`
气血精：`qi/max_qi`、`jing/max_jing`、`jingli/max_jingli`、`eff_qi`、`eff_jing`
内力：`neili/max_neili`
状态条：气/精/神 三条比例条（当前/上限着色）
主元：`food(0-300)`、`water(0-300)`、`fighter_spirit`(战意)、`is_busy`(`"true"/"false"`)、`is_fighting`
属性四维（技能 dock，旧版字段名 bug 修复）：`str`=膂力、`dex`=身法(注意不是 agi)、`int`=悟性、`con`=根骨、`per`=容貌
特殊：`eff_qi/eff_jing` 有效气血（中毒/受伤）并入状态条。

**测试用字素**：`vigor/qi`、`vigor/yuan`（真气/真元，新功能，可能为 null，按 null 容忍）。

**房间（Room dock 数据源 = GMCP.Move，无独立 Room.Info）**：
- 字段：`result("true"/"false")`、`dir(string[])`、`short`(目标房间中文名)。
- 处理：仅 `result==true` 更新房间；`short` 有值则更新 `room_name`；`dir` 更新 `exits`。`result=false`(撞墙)不更新。
- 联动：移动 dock 的八方向按钮/Estado 由 `exits` 驱动（同 B9）；GM.Move 成功后如有新房间触发静默 `look` 补 desc/exits 推给地图（可选）。

## Combat 模块（订阅，字段理论值，真战斗后验证）
`enemy_in`、`enemy_out`、`qi_damage`、`jing_damage`(因为 jing_damage)、`eff_qi_pct`、`eff_jing_pct`(0-100)、`perform_name`、`perform_cd`、`perform_id`
UI：显示战斗中敌人、伤害(`气血 -X`)、绝招名+CD。
验证点：enemy 是中文名或 ID、pct 是百分比还是绝对值、cd 是剩余还是总时长。

## Message / Buff（旧版丢弃，新客户端照常实现）
- `GMCP.Message`：`channel(QQ)`、`type(pic/mp4)`、`name`、`url`。集成到聊天区：加「📷 QQ群图片」频道过滤器，解析后显示缩略图/下载链接。
- `GMCP.Buff`：服务器当前只推 `is_end/name(null)/terminated(completed)`，无法 buff 监控，保留订阅观察。

## System
- 仅登录推一次 `{"site":""}`，占位，仅 log。

## 调试
- GMCP 收发日志：`%APPDATA%\XkxClient\gmcp_recv.log`（`<module>: <JSON>` 每行）。
- 手动开关：`tune gmcp <Status|Move|System|Combat|Message|Buff> on/off`。

## 其他：Status 字段映射到 docker
- 状态 dock 字段映射对照见上；四维属性修复（见「包装将模块展示位 dock」）并入属性 dock。

## 验收
- [ ] 握手：收到 WILL 0xC9 → DO+Core.Hello；无 WILL 也主动发；登录后自动 tune 订阅
- [ ] 解析容忍 ANSI + 双重 JSON；Status 高达 92% 推送不重绘 dock，仅局部更新
- [ ] Status 全字段根级正确映射（str/dex/int/con/per 修复）
- [ ] Move 驱动状态 dock / 移动 dock / 地图，result=false 不更新
- [ ] 全频道订阅生效；Message 图/视频入聊天区，Buff/Combat 按实测验证

## 依据源码参考（旧客户端）
`core/gmcp_handlers.py`、`core/state.py`、`network/telnet_protocol.py`、`network/connection.py`