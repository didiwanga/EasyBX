from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from xkxclient.core.config import json_read
from xkxclient.core.resources import PROJECT_ROOT

# B6：内置命令字典（分类），每项 (name, aliases, desc, cat)
# 数据源：commands_index_v1.0（北大侠客行客户端 v2.6 内置索引，126 条/12 类）
_COMMANDS: list[tuple[str, list, str, str]] = [
    # ---- 移动 ----
    ("n", ["north"], "向北走一步", "移动"),
    ("s", ["south"], "向南走一步", "移动"),
    ("e", ["east"], "向东走一步", "移动"),
    ("w", ["west"], "向西走一步", "移动"),
    ("u", ["up"], "向上", "移动"),
    ("d", ["down"], "向下", "移动"),
    ("nu", ["northup"], "向北上", "移动"),
    ("nd", ["northdown"], "向北下", "移动"),
    ("su", ["southup"], "向北上", "移动"),
    ("sd", ["southdown"], "向南下", "移动"),
    ("eu", ["eastup"], "向东上", "移动"),
    ("ed", ["eastdown"], "向东下", "移动"),
    ("wu", ["westup"], "向西上", "移动"),
    ("wd", ["westdown"], "向西下", "移动"),
    ("ne", ["northeast"], "向东北", "移动"),
    ("nw", ["northwest"], "向西北", "移动"),
    ("se", ["southeast"], "向东南", "移动"),
    ("sw", ["southwest"], "向西南", "移动"),
    ("enter", [], "进入", "移动"),
    ("out", [], "外出", "移动"),
    ("goto", [], "移动:长途走表", "移动"),
    # ---- 查看 ----
    ("look", ["l"], "查看当前房间或某物/某人", "查看"),
    ("examine", ["exa"], "查看物品详细信息", "查看"),
    ("inventory", ["i"], "查看背包", "查看"),
    ("score", [], "查看角色分数/属性", "查看"),
    ("hp", [], "查看精气神", "查看"),
    ("skills", ["cha"], "查看已学技能列表", "查看"),
    ("mapskills", [], "查看技能等级映射", "查看"),
    ("who", [], "查看在线玩家列表", "查看"),
    ("title", [], "查看头衔", "查看"),
    ("team", [], "查看队伍状态", "查看"),
    ("compare", [], "与某人比较武功", "查看"),
    ("top", [], "查看排行榜", "查看"),
    ("time", [], "看看现在的时间", "查看"),
    ("weather", [], "查看天气", "查看"),
    # ---- 物品 ----
    ("get", [], "拾取物品, 例 get sword from corpse", "物品"),
    ("drop", [], "丢弃物品", "物品"),
    ("give", [], "给某人物品, 例 give sword to 张三", "物品"),
    ("put", [], "放入容器, 例 put sword in bag", "物品"),
    ("eat", [], "食用", "物品"),
    ("drink", [], "饮用", "物品"),
    ("use", [], "使用物品", "物品"),
    ("open", [], "打开门/箱", "物品"),
    ("close", [], "关闭门/箱", "物品"),
    ("wield", [], "装备武器", "物品"),
    ("unwield", [], "卸下武器", "物品"),
    ("wear", [], "穿上装备", "物品"),
    ("remove", [], "脱下装备", "物品"),
    ("id", [], "查看物品详情", "物品"),
    # ---- 战斗 ----
    ("kill", ["k"], "攻击某人", "战斗"),
    ("fight", [], "切磋 (点到为止)", "战斗"),
    ("hit", [], "攻击敌人", "战斗"),
    ("halt", [], "停止战斗", "战斗"),
    ("perform", ["pfm"], "施展绝招, 例 perform jiudiesier", "战斗"),
    ("enable", ["jifa"], "激发技能, 例 enable sword.chan", "战斗"),
    ("enforce", ["jiali"], "加内力点数", "战斗"),
    ("exert", ["yun"], "运内功, 例 yun recover", "战斗"),
    ("yun regenerate", [], "恢复精", "战斗"),
    ("yun recover", [], "恢复气", "战斗"),
    ("yun heal", [], "自己疗伤", "战斗"),
    ("guard", [], "守住某方向", "战斗"),
    ("surrender", [], "投降", "战斗"),
    ("flee", [], "逃跑", "战斗"),
    ("prepare", [], "准备招式", "战斗"),
    ("special", [], "查看技能招式", "战斗"),
    ("wimpy", [], "设定逃脱气血", "战斗"),
    ("setwimpy", [], "设定逃脱条件", "战斗"),
    # ---- 修炼 ----
    ("dazuo", ["dz"], "打坐, 练内力", "修炼"),
    ("lian", ["practice"], "练习技能", "修炼"),
    ("study", ["du"], "读书学习", "修炼"),
    ("xiulian", [], "修炼技能", "修炼"),
    ("learn", [], "向师傅学艺", "修炼"),
    ("meditate", [], "打坐", "修炼"),
    ("exercise", [], "练功", "修炼"),
    ("wushu", [], "查看武学", "修炼"),
    # ---- 任务 ----
    ("ask", [], "与 NPC 对话触发任务, 例 ask npc about topic", "任务"),
    ("fullme", [], "弹出验证码图片", "任务"),
    # ---- 拱猪 ----
    ("sit", [], "加入牌局 (桌长自动)", "拱猪"),
    ("bid", [], "卖牌, 例 bid sq", "拱猪"),
    ("sell", [], "卖牌, 同 bid", "拱猪"),
    ("pass", [], "卖牌结束", "拱猪"),
    ("play", [], "出牌, 例 play d7", "拱猪"),
    ("deal", [], "桌长发牌命令", "拱猪"),
    ("skip", [], "桌长跳过本副牌", "拱猪"),
    ("claim", [], "全收要求 (同意/否决), 例 claim yes", "拱猪"),
    ("leave", [], "离开牌局", "拱猪"),
    ("l table", [], "查看牌桌", "拱猪"),
    ("l scoreboard", [], "查看积分", "拱猪"),
    # ---- 特殊 ----
    ("emote", [], "执行聊天指令 (1651 种 emotes), 例 emote coffin 或 :coffin", "特殊"),
    ("cemote", [], "cemote + 文本 (门派风格)", "特殊"),
    ("alias", [], "查看当前别名列表", "特殊"),
    ("action", [], "客户端定义触发器 (MUD 函数)", "特殊"),
    ("go", [], "自动寻路 (MUD 函数)", "特殊"),
    ("xy", [], "查询当前位置坐标 (MUD 函数)", "特殊"),
    # ---- 环境变量 ----
    ("set", [], "设置环境变量, 例 set brief 1", "环境变量"),
    ("unset", [], "删除环境变量", "环境变量"),
    ("env", [], "查看环境变量", "环境变量"),
    ("set brief 1", [], "brief 模式: 省略出口", "环境变量"),
    ("set brief 2", [], "brief 模式: 显示出口+物品", "环境变量"),
    ("set brief 3", [], "brief 模式: 即时地图+出口+物品", "环境变量"),
    ("set no_accept 1", [], "不接受别人 give 物品", "环境变量"),
    ("set skip_combat 1", [], "屏蔽普通招式信息", "环境变量"),
    ("set skip_combat 2", [], "屏蔽 1 + 回合和互搏提示", "环境变量"),
    ("set skip_combat 3", [], "屏蔽受伤外的所有信息", "环境变量"),
    ("set skip_combat 4", [], "屏蔽所有战斗信息", "环境变量"),
    ("set broadcast_combat 1", [], "接收他人战斗信息", "环境变量"),
    ("set no_teach 1", [], "不教徒弟技能", "环境变量"),
    ("set refuse_tell 1", [], "拒绝别人 tell", "环境变量"),
    ("set wimpy 30", [], "气血低于 30% 自动逃命", "环境变量"),
    ("set learn_emote 1", [], "显示闲聊频道中别人动作的 emote", "环境变量"),
    ("set kill_msg", [], "下 kill 时说的话", "环境变量"),
    ("set no_autosave 1", [], "关闭自动存档", "环境变量"),
    ("set nowieldmsg 1", [], "不接收他人 wield/unwield 信息", "环境变量"),
    ("set food 1", [], "可食用房间内物品", "环境变量"),
    ("set custom_hp 1", [], "HP 显示方式 1", "环境变量"),
    ("set custom_hp 2", [], "HP 显示方式 2", "环境变量"),
    ("set forcedact 1", [], "蓄势不足也可强行发动绝招", "环境变量"),
    # ---- 社交 ----
    ("say", ["'"], "说话, 同房间人听到", "社交"),
    ("tell", [], "私聊某人, 例 tell 张三 hi", "社交"),
    ("reply", ["re"], "回复上一条 tell", "社交"),
    ("whisper", ["w"], "耳语, 同房间才能收到", "社交"),
    ("chat", [], "进入聊天频道 (默认进闲聊)", "社交"),
    ("chat1", [], "进入【闲聊】频道 (默认启用)", "社交"),
    ("chat2", [], "进入【北侠QQ群】频道 (默认启用)", "社交"),
    ("chat3", [], "进入【谣言】频道", "社交"),
    ("chat4", [], "进入【交易】频道", "社交"),
    ("chat5", [], "进入【江湖】频道", "社交"),
    ("rumor", [], "进入【谣言】频道 (简写)", "社交"),
    ("note", [], "给某人留言 (离线也能收到)", "社交"),
    ("notes", [], "查看我的留言", "社交"),
    ("finger", ["f"], "查看玩家信息 (等级/门派/上次登录)", "社交"),
    ("beep", [], "呼叫某人", "社交"),
    ("gtalk", ["g"], "帮派频道", "社交"),
    ("party", ["p"], "队伍频道", "社交"),
    ("family", [], "师门频道", "社交"),
    ("shimen", [], "查看师门信息", "社交"),
    ("bow", [], "鞠躬 (emote)", "社交"),
    ("nod", [], "点头 (emote)", "社交"),
    ("shake", [], "摇头 (emote)", "社交"),
    ("wave", [], "挥手 (emote)", "社交"),
    ("smile", [], "微笑 (emote)", "社交"),
    ("cry", [], "哭泣 (emote)", "社交"),
    ("hug", [], "拥抱 (emote)", "社交"),
    ("haha", [], "大笑 (emote)", "社交"),
    ("helpme", [], "求助", "社交"),
    # ---- 系统 ----
    ("help", [], "查看帮助, 例 help emote", "系统"),
    ("save", [], "保存角色", "系统"),
    ("quit", [], "退出游戏", "系统"),
    ("news", [], "浏览新闻/消息", "系统"),
    ("vip", [], "会员状态", "系统"),
    ("bug", [], "报告错误", "系统"),
    ("favor", [], "查看声望", "系统"),
    ("yes", [], "确认", "系统"),
    ("no", [], "取消", "系统"),
    # ---- 队伍 ----
    ("team form", [], "组建队伍", "队伍"),
    ("team follow", [], "跟随某人", "队伍"),
    ("team dismiss", [], "解散队伍", "队伍"),
    ("team kick", [], "踢出队员 (仅队长)", "队伍"),
    ("team add", [], "邀请某人入队", "队伍"),
    ("team leave", [], "离开队伍", "队伍"),
    ("join", [], "加入队伍", "队伍"),
    ("invite", [], "邀请加入队伍", "队伍"),
    ("promote", [], "升为队长", "队伍"),
    ("assign", [], "队员转职", "队伍"),
]
_DIRS = ["north", "south", "east", "west", "northeast", "northwest", "southeast",
         "southwest", "up", "down", "northup", "northdown", "southup", "southdown",
         "eastup", "eastdown", "westup", "westdown", "enter", "out"]
_CAT_ORDER = ["移动", "查看", "物品", "战斗", "修炼", "任务", "拱猪", "特殊",
              "环境变量", "社交", "系统", "队伍", "表情"]


class CommandStore:
    """B6 命令字典：内置命令 + emotes.json（可运行刷新）。"""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        for name, aliases, desc, cat in _COMMANDS:
            self.entries.append({
                "name": name, "aliases": list(aliases),
                "desc": desc, "cat": cat, "is_emote": False,
            })
        self._load_emotes()

    def _load_emotes(self) -> None:
        path = PROJECT_ROOT / "resources" / "emotes.json"
        data = json_read(path)
        items = data.get("emotes") if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []
        seen = set()
        for it in items:
            if not isinstance(it, dict):
                continue
            # 服务器字段: verb/myself/others/...（数据源 pkuxkx GetEmotePage）
            name = str(it.get("name") or it.get("verb") or it.get("emote") or "")
            if name and name not in seen:
                seen.add(name)
                desc = str(it.get("desc") or it.get("description")
                           or it.get("meaning") or it.get("othersSelf")
                           or it.get("others") or "").replace("$N", "别人").replace("$P", "自己")
                self.entries.append({
                    "name": name, "aliases": [], "is_emote": True,
                    "desc": desc,
                    "cat": "表情",
                })
        if not self.entries or not any(e["cat"] == "表情" for e in self.entries):
            for e in ["hi", "bye", "cheer", "applaud", "nod", "shake", "grin", "faint",
                      "kick", "pat", "bow", "hug", "wave", "yawn", "smile", "frown"]:
                self.entries.append({"name": e, "aliases": [], "desc": "emote " + e,
                                     "cat": "表情", "is_emote": True})

    def categories(self) -> list[str]:
        seen = set()
        out = []
        for cat in _CAT_ORDER:
            if any(e["cat"] == cat for e in self.entries) and cat not in seen:
                out.append(cat)
                seen.add(cat)
        for e in self.entries:
            if e["cat"] not in seen:
                out.append(e["cat"])
                seen.add(e["cat"])
        return out

    def search(self, keyword: str) -> list[dict]:
        kw = keyword.strip().lower()
        if not kw:
            return self.entries
        out = []
        for e in self.entries:
            hay = e.get("name", "") + " " + " ".join(e.get("aliases") or []) + " " + e.get("desc", "")
            if kw in hay.lower():
                out.append(e)
        return out

    def prefix_candidates(self, prefix: str) -> list[str]:
        p = prefix.lower()
        names = [e["name"] for e in self.entries
                 if e["name"].lower().startswith(p)
                 or any(a.lower().startswith(p) for a in e.get("aliases") or [])]
        dirs = [d for d in _DIRS if d.startswith(p)]
        return list(dict.fromkeys(names + dirs))


class CommandPanel(QWidget):
    """命令速查停靠面板（B6）：搜索 + 分类树 + 表情。单击填 / 双击发。"""

    fill_requested = pyqtSignal(str)
    send_requested = pyqtSignal(str)

    def __init__(self, store: CommandStore = None, parent=None) -> None:
        super().__init__(parent)
        self.store = store if store is not None else CommandStore()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜命令/别名/描述…")
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.search.textChanged.connect(self._on_search)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(self.search)
        lay.addWidget(self.tree, 1)

        self._rebuild()
        self.tree.itemDoubleClicked.connect(self._on_double)
        self.tree.itemClicked.connect(self._on_click)

    def _rebuild(self) -> None:
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for e in self.store.entries:
            cat = e.get("cat", "其他")
            if cat not in groups:
                grp = QTreeWidgetItem([cat])
                self.tree.addTopLevelItem(grp)
                groups[cat] = grp
            label = e.get("name", "")
            aliases = e.get("aliases") or []
            if aliases:
                label += " [" + ",".join(aliases) + "]"
            item = QTreeWidgetItem([label])
            item.setToolTip(0, e.get("desc", ""))
            if e.get("is_emote"):
                item.setForeground(0, Qt.GlobalColor.gray)
            item.setData(0, Qt.ItemDataRole.UserRole, e)
            groups[cat].addChild(item)
        self.tree.expandAll()

    def _on_search(self, text: str) -> None:
        if text.strip():
            # PyQt6 的 setData 存的是 dict 副本，不能用 id() 匹配项目；
            # 直接用名称匹配（命令字典 name 唯一）。
            matched_names = {e.get("name", "") for e in self.store.search(text)}
        else:
            matched_names = None
        for i in range(self.tree.topLevelItemCount()):
            grp = self.tree.topLevelItem(i)
            for j in range(grp.childCount()):
                item = grp.child(j)
                e = item.data(0, Qt.ItemDataRole.UserRole) or {}
                show = matched_names is None or e.get("name") in matched_names
                item.setHidden(not show)
                if not show:
                    continue
                parent = item.parent()
                while parent is not None:
                    parent.setHidden(False)
                    parent = parent.parent()
        self.tree.expandAll()

    def _on_click(self, item: QTreeWidgetItem, _col: int) -> None:
        e = item.data(0, Qt.ItemDataRole.UserRole)
        if e:
            self.fill_requested.emit(e.get("name", ""))

    def _on_double(self, item: QTreeWidgetItem, _col: int) -> None:
        e = item.data(0, Qt.ItemDataRole.UserRole)
        if e:
            self.send_requested.emit(e.get("name", ""))
