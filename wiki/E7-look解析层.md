# Look 解析层（房间 & 实体 & 状态技能，共享解析）

> 从 `look` 命令返回的**原始文本**中提取结构化信息，供地图、房间详情、实体搜索、自动化（变量池）共用。
> 触发方式以**进入新房间自动 look**（省流不刷屏，可配置）为主，兼手动/右键。解析一次、处处复用。

## 一、解析内容（三类）
### 1. 房间结构 RoomStructure
- 房间名（`【这里】`/`大草原` 行首标题）
- 描述段落（多行，到分隔符前）
- 出口出口块 lines（`这里明显的出口是：north, east...`）
- 容器段（`你可以看到/容器：` 等）
- 房间是「室内/室外/洞/城」等典型分类线索

### 2. 实体列表 Entities（NPC/玩家/物品）
- 每行「描述 + 名称」冒号（`这里是……：张三 土狼`）
- 识别类型：玩家 `玩家` / NPC(带头衔) / 物品(可拾取工具 `进入/move`) 
- 用结构化 list：`[{type, name, head, desc}]`，**支持合并重叠**

### 3. 状态技能解析 StatusSkill
- 身上 buff/debuff、状态行（`【剧情】/【buff】`）
- 技能/特殊防御/装备（可扩展，见技能面板需求）
- 可复用的通用解析器（与触发器的「捕获」同源）

## 二、触发方式
| 触发 | 说明 | 是否省流 |
|---|---|---|
| **新房间自动**（主触发） | 首次进入房间自动 look（GMCP.Move 判定新房间），可配置开关 | 开 |
| 手动按钮/右键 | 主动 look 当前房间 | 手动 |
| 定时轮询 | 每隔 N 秒 look（如实轮廓，留后台） | 可关 |

> 自动主动 look 会刷新「房间内实体」状态，只在进入新房间触发，不刷屏、不打扰（主输出隐藏）。

## 三、输出分类（去哪）
| 去向 | 内容 |
|---|---|
| 房间详情面板（地图） | RoomStructure + EntityList |
| 查询面板 | EntityList 搜索（NPC/物品/玩家） |
| 触发域变量池 | 最新实体/房间状态写变量：`{room.name}`/`{实体}`/... 供 B3/B3c/脚本调用（变量格式统一 `{}`，见 B3）|
| 事件总线 | 发 `look.parsed` 事件，供 Lua 等订阅 |

## 四、架构（重写，不沿用旧版）
- `core/look/__init__`：公开 `parse_room_text(text) -> LookResult`
- `LookCapture`（QObject）：
  - `start/stop`
  - 监听 `GMCP.Move`（result=true）判断新房间是否与上次不同 → 进入新房间才触发。
  - `_on_idle/_timeout` 定时再次 look（可选，防属性丢失）。
  - 收集缓冲多行 → 一次性 `parse_room_text` → 分发。
- 解析器：
  - `room.py`：结构
  - `entity.py`：实体
  - `status.py`：状态技能

- 与其它模块互不干扰（不阻塞 when）。

## 五、数据结构
```python
class RoomStructure:
    name: str
    desc: list[str]          # 描述行
    exits: list[str]         # 出口方向
    category: str            # 室/城/野...

class Entity:
    name: str
    kind: str                # npc | player | item
    head: str = ""
    desc: str = ""

class LookResult:
    room: RoomStructure | None
    entities: list[Entity]
    status: dict             # buff/技能/状态
    raw: list[str]
```

## 六、与地图联动
- 解析出的 exit 交给地图同步引擎（补充 MapSync 的出口图形）。
- 实体/描述写回地图房间信息。
- **接口预留**：地图系统的「房间细节采集」复用此解析，不单独再写一套（见 map 开放留接口）。

## 七、验收
- [ ] 进入新房间自动 look（可关开关），不刷屏
- [ ] 解析房间结构/实体/状态三类正确（重叠实体、出口解析）
- [ ] 输出到全部去向（详情/搜索/变量池/事件总线）
- [ ] 手动触发可用
- [ ] 编码 UTF-8，中文正确