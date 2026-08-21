"""迷宫路径大全（玩家整理的确定性走法）。

地图寻路找不到路时，搜目的地可走预置路径直达。数据来源：玩家整理的
「迷宫路径大全」。两类条目：

- MAZE_ROUTES：有确定走法，可点「行走」直接执行（步骤里的特殊命令
  enter/swim/ban/give/break 等与方向一样按步延时发送，由 send_auto 的
  特殊命令解析处理 `#N 重复`/`#wa 延时`）。
- MAZE_GUIDES：无固定走法，靠 look/观察循环尝试，仅展示文字说明。

条目字段：
  id     唯一标识
  region 区域（用于选择时区分同名目的地）
  from   起点
  to     终点
  targets 搜索命中名（用户搜这些名字时给出该走法）
  hint   起点提示（与路径一起展示）
  steps  走法步骤（MAZE_ROUTES 必有；MAZE_GUIDES 无）
  text   附加说明（可选，展示在路径下方）
"""

from __future__ import annotations


MAZE_ROUTES: list[dict] = [
    # ---- 武当 ----
    {
        "id": "wudang_bamboo",
        "region": "武当",
        "from": "北侧门", "to": "竹林小路",
        "targets": ["竹林小路", "武当竹林小路"],
        "hint": "起点：武当北侧门",
        "steps": ["w", "sw", "s", "se", "e", "ne", "n", "nw", "sw", "s", "e", "n"],
    },
    # ---- 少林 ----
    {
        "id": "huangmei_qianfodian",
        "region": "少林",
        "from": "黄眉僧处", "to": "千佛殿",
        "targets": ["千佛殿"],
        "hint": "起点：黄眉僧处",
        "steps": ["wd", "nw", "wu", "sw", "su", "sw", "wu", "nw", "nu", "u",
                  "nu", "nw", "wu", "sw", "nw", "wd", "sw", "sd", "ne", "nd",
                  "nw", "wd", "nu", "ne", "eu", "se", "se", "eu", "ne", "nu",
                  "ne", "nu", "nw", "wu", "se", "ed", "ne", "nd", "ed", "ne",
                  "nd", "nw", "n", "ne", "ed", "se", "sd", "w", "sw", "s",
                  "se", "e", "se", "s", "sw", "s"],
    },
    {
        "id": "mogao_chitao",
        "region": "莫高窟",
        "from": "莫高窟", "to": "赤套渡口",
        "targets": ["赤套渡口"],
        "hint": "起点：莫高窟；走到月儿泉后 enter hole",
        "steps": ["nw", "w", "nw", "nu", "nw", "nu", "ne", "n", "w", "nw",
                  "w", "nw", "nw", "w", "nw", "w", "w", "w", "sw", "sw",
                  "w", "w", "s", "s", "s", "s", "w", "enter hole"],
    },
    {
        "id": "qianfo_chuzuan",
        "region": "少林",
        "from": "千佛殿", "to": "初祖庵",
        "targets": ["初祖庵"],
        "hint": "起点：千佛殿",
        "steps": ["n", "ne", "n", "nw", "w", "nw", "n", "ne", "e", "nu",
                  "nw", "wu", "sw", "wu", "nw", "nu", "ne", "u", "ed", "ne",
                  "nd", "nw", "nd", "ne", "ed", "se", "d"],
    },
    {
        "id": "chuzuan_qianfo",
        "region": "少林",
        "from": "初祖庵", "to": "千佛殿",
        "targets": ["千佛殿"],
        "hint": "起点：初祖庵",
        "steps": ["u", "nw", "wu", "sw", "su", "se", "su", "sw", "wu", "d",
                  "sw", "sd", "se", "ed", "ne", "ed", "se", "sd", "w", "sw",
                  "s", "se", "e", "se", "s", "sw", "s"],
    },
    {
        "id": "chuzuan_damo",
        "region": "少林",
        "from": "初祖庵", "to": "达摩洞",
        "targets": ["达摩洞"],
        "hint": "起点：初祖庵",
        "steps": ["nu", "nw", "wu", "sw", "eu", "ne", "nu", "nw", "ne", "e",
                  "se", "s", "e", "sw", "se", "n", "s", "w", "e", "w", "e",
                  "e", "s", "w", "n", "nw", "n"],
    },
    {
        "id": "damo_chuzuan",
        "region": "少林",
        "from": "达摩洞", "to": "初祖庵",
        "targets": ["初祖庵"],
        "hint": "起点：达摩洞",
        "steps": ["out", "w", "n", "nw", "w", "sw", "se", "sd", "sw", "wd",
                  "ne", "ed", "se", "sd"],
    },
    {
        "id": "wuxingdong",
        "region": "少林",
        "from": "少林监狱（红色五行洞）", "to": "五行洞出口",
        "targets": ["五行洞"],
        "hint": "起点：少林监狱一直 s 到红色五行洞后使用",
        "steps": ["n", "w", "n", "e", "s", "u", "out"],
    },
    {
        "id": "shaolin_shilin",
        "region": "少林",
        "from": "少林大门外石阶", "to": "松树林",
        "targets": ["松树林"],
        "hint": "起点：少林大门外石阶",
        "steps": ["se", "s", "se", "e", "ne", "n", "ne", "e", "se", "ne",
                  "e", "se", "s", "nw", "n", "ne", "e", "nw", "nd", "ne",
                  "ed", "nd", "ne", "ed", "se", "w", "nw", "n", "ne", "n",
                  "se", "e", "ne", "n", "e"],
    },
    {
        "id": "shilin_qingyun",
        "region": "少林",
        "from": "松树林黄金处", "to": "青云坪",
        "targets": ["青云坪"],
        "hint": "起点：松树林黄金处",
        "steps": ["w", "e", "s", "e", "n", "n", "e", "w", "s"],
    },
    {
        "id": "qingyun_qianfo",
        "region": "少林",
        "from": "青云坪", "to": "千佛殿",
        "targets": ["千佛殿"],
        "hint": "起点：青云坪",
        "steps": ["nw", "sw", "wu", "nw", "nu", "d", "nd", "nw", "wd", "sw",
                  "sd", "sw", "wd", "nw", "s", "sw", "w", "nw", "w", "se",
                  "su", "sw", "wu", "s", "sw", "w", "nw", "nd", "nw", "wd",
                  "sw", "se", "sd", "sw", "wd", "sw", "e", "se", "s", "sw", "s"],
    },
    # ---- 峨眉 ----
    {
        "id": "emei_jinding",
        "region": "峨眉",
        "from": "云海入口", "to": "金顶",
        "targets": ["金顶"],
        "hint": "起点：峨眉云海入口",
        "steps": ["n", "n", "w", "e", "s", "e", "e", "n", "n"],
    },
    # ---- 归云庄 ----
    {
        "id": "guiyunzhuang_baoku",
        "region": "归云庄",
        "from": "迷魂阵入口", "to": "宝库",
        "targets": ["宝库", "归云庄宝库"],
        "hint": "起点：归云庄迷魂阵入口",
        "text": "若已经在迷魂阵里面，省略第一步 give banana to ju yuan",
        "steps": ["give banana to ju yuan", "s", "e", "n", "w", "s", "w", "e",
                  "e", "e", "n", "break men"],
    },
    {
        "id": "guiyunzhuang_baoku_in",
        "region": "归云庄",
        "from": "迷魂阵内", "to": "宝库",
        "targets": ["宝库", "归云庄宝库"],
        "hint": "起点：已在归云庄迷魂阵里面",
        "steps": ["e", "n", "w", "s", "w", "e", "e", "e", "n", "break men", "s"],
    },
    {
        "id": "guiyunzhuang_yinzhe",
        "region": "归云庄",
        "from": "归云庄树林", "to": "隐者居",
        "targets": ["隐者居"],
        "hint": "起点：归云庄树林",
        "steps": ["s", "w", "w", "e", "s"],
    },
    {
        "id": "yinzhe_guiyunzhuang",
        "region": "归云庄",
        "from": "隐者居", "to": "归云庄树林",
        "targets": ["归云庄树林"],
        "hint": "起点：隐者居",
        "steps": ["#5 n"],
    },
    # ---- 杀手榜果林 ----
    {
        "id": "guolin_houshan",
        "region": "杀手榜果林",
        "from": "大道", "to": "后山",
        "targets": ["后山", "杀手榜果林"],
        "hint": "起点：大道",
        "steps": ["n", "s", "s", "w", "s", "s", "s"],
    },
    {
        "id": "houshan_dadao",
        "region": "杀手榜果林",
        "from": "后山", "to": "大道",
        "targets": ["大道", "杀手榜果林后山"],
        "hint": "起点：后山；多个 e 是为了遍历 npc",
        "text": "一直 e 直到看到 south 方向有大道即可出来；暴力出来就是 s;s;s;s",
        "steps": ["s", "e", "e", "e", "e", "e", "e"],
    },
    # ---- 古墓 ----
    {
        "id": "gumu_mishi",
        "region": "古墓",
        "from": "古墓小溪", "to": "古墓密室",
        "targets": ["古墓密室"],
        "hint": "起点：古墓小溪；swim river 游泳消耗气，气不够会淹死",
        "steps": ["swim river", "n", "n", "e", "n", "n", "w", "w", "n", "n",
                  "e", "n", "w", "ban stone", "w", "w"],
    },
    {
        "id": "gumu_jiuyin",
        "region": "古墓",
        "from": "古墓小溪", "to": "九阴真经藏经处",
        "targets": ["九阴真经藏经处", "藏经处"],
        "hint": "起点：古墓小溪；swim river 游泳消耗气",
        "text": "需先找杨女 ask 九阴真经 并给她鞭法秘籍（从李莫愁身上取得）",
        "steps": ["swim river", "#2 n", "e", "#2 n", "w", "enter xuanwo"],
    },
    {
        "id": "jiuyin_gumu",
        "region": "古墓",
        "from": "九阴真经藏经处", "to": "古墓小溪",
        "targets": ["古墓小溪"],
        "hint": "起点：九阴真经藏经处",
        "steps": ["out", "s", "s", "out"],
    },
    {
        "id": "gumu_mudong",
        "region": "古墓",
        "from": "古墓小石洞", "to": "九阴真经墓洞",
        "targets": ["九阴真经墓洞", "墓洞"],
        "hint": "起点：古墓小石洞；向小龙女打听机关即可",
        "text": "到达放棺材的大厅后 juan picture 可单向回到放寒玉床的侧室",
        "steps": ["ban stone", "e", "w", "s", "s", "w", "e", "enter guancai5", "down"],
    },
    # ---- 天地会暗道 ----
    {
        "id": "tiandihui_an",
        "region": "天地会暗道",
        "from": "棺材（地宫暗道）", "to": "大厅",
        "targets": ["天地会暗道", "天地会暗道大厅"],
        "hint": "起点：地宫暗道棺材处，先 knock guancai 3",
        "steps": ["knock guancai 3", "ed", "s", "s", "s", "w", "e", "n", "n", "n", "n"],
    },
    {
        "id": "an_guancai",
        "region": "天地会暗道",
        "from": "大厅", "to": "棺材（地宫暗道）",
        "targets": ["天地会暗道棺材", "天地会暗道出口"],
        "hint": "起点：大厅",
        "steps": ["s", "s", "s", "s", "w", "n", "n", "e", "n", "s", "wu"],
    },
    # ---- 南疆沙漠 / 回族小镇 ----
    {
        "id": "nanjiang_lvzhou",
        "region": "南疆沙漠",
        "from": "天山脚下", "to": "沙漠绿洲",
        "targets": ["沙漠绿洲"],
        "hint": "起点：天山脚下",
        "steps": ["sw", "nw", "nw", "sw", "se", "ne"],
    },
    {
        "id": "huizu_lvzhou",
        "region": "回族小镇小沙漠",
        "from": "小山坡旁的入口", "to": "沙漠绿洲",
        "targets": ["沙漠绿洲"],
        "hint": "起点：小山坡旁入口",
        "steps": ["nw", "sw", "se", "ne"],
    },
    # ---- 慕容茶花林 ----
    {
        "id": "murong_xiaolu",
        "region": "慕容茶花林",
        "from": "码头", "to": "小路",
        "targets": ["茶花林小路", "慕容茶花林"],
        "hint": "起点：码头",
        "steps": ["w", "n", "w", "s", "s", "e", "e", "w", "w"],
    },
    {
        "id": "xiaolu_matou",
        "region": "慕容茶花林",
        "from": "小路", "to": "码头",
        "targets": ["茶花林码头"],
        "hint": "起点：小路",
        "steps": ["e", "s", "n", "w", "n", "n", "e", "s", "e"],
    },
    {
        "id": "xiaolu_hongxiage",
        "region": "慕容茶花林",
        "from": "小路", "to": "红霞阁",
        "targets": ["红霞阁"],
        "hint": "起点：小路",
        "steps": ["e", "s", "w"],
    },
    # ---- 冰火岛 ----
    {
        "id": "binghuodao_xiexun",
        "region": "冰火岛",
        "from": "树林入口", "to": "谢逊处",
        "targets": ["谢逊", "谢逊处"],
        "hint": "起点：树林入口",
        "steps": ["n", "e", "n", "w", "n", "s", "e", "e", "n", "n"],
    },
    {
        "id": "xiexun_rukou",
        "region": "冰火岛",
        "from": "谢逊处", "to": "树林入口",
        "targets": ["冰火岛树林"],
        "hint": "起点：谢逊处",
        "steps": ["s", "s", "s", "w", "e", "n", "nw"],
    },
    # ---- 无量山 ----
    {
        "id": "wuliangshan_yubi",
        "region": "无量山",
        "from": "迷魂阵入口", "to": "玉璧",
        "targets": ["玉璧", "无量山玉璧"],
        "hint": "起点：迷魂阵入口",
        "steps": ["s", "e", "n", "w"],
    },
    {
        "id": "yubi_rukou",
        "region": "无量山",
        "from": "玉璧", "to": "迷魂阵入口",
        "targets": ["无量山迷魂阵"],
        "hint": "起点：玉璧",
        "steps": ["#10 e"],
    },
    # ---- 明教 / 白驼山 ----
    {
        "id": "mingjiao_shanjiao",
        "region": "明教大沙漠",
        "from": "渡口", "to": "山脚下",
        "targets": ["山脚下", "明教山脚"],
        "hint": "起点：渡口",
        "steps": ["#7 w", "#3 n", "#3 w"],
    },
    {
        "id": "shanjiao_dukou",
        "region": "明教大沙漠",
        "from": "山脚下", "to": "渡口",
        "targets": ["明教大沙漠"],
        "hint": "起点：山脚下",
        "steps": ["#4 e", "#4 s", "#4 e"],
    },
    {
        "id": "baituoshan_gebi",
        "region": "白驼山丝绸之路",
        "from": "丝绸之路", "to": "戈壁",
        "targets": ["戈壁"],
        "hint": "起点：丝绸之路",
        "steps": ["#11 w"],
    },
    {
        "id": "gebi_sichou",
        "region": "白驼山丝绸之路",
        "from": "戈壁", "to": "丝绸之路",
        "targets": ["丝绸之路"],
        "hint": "起点：戈壁",
        "text": "经验低时会提示需要骆驼，到扬州钱庄 ask qian about 租骆驼",
        "steps": ["#4 e", "s", "#6 e"],
    },
    # ---- 张翠山→张三丰 ----
    {
        "id": "zhangcuishan_zhangsanfeng",
        "region": "武当",
        "from": "张翠山处", "to": "张三丰处",
        "targets": ["张三丰处", "张三丰"],
        "hint": "起点：张翠山处",
        "steps": ["w", "sw", "s", "se", "e", "ne", "n", "nw", "sw", "s", "e",
                  "open door", "n"],
    },
    # ---- 神龙岛 ----
    {
        "id": "shenlong_shanjiao",
        "region": "神龙岛",
        "from": "海滩", "to": "山脚",
        "targets": ["山脚", "神龙岛山脚"],
        "hint": "起点：海滩",
        "steps": ["#10 e", "e"],
    },
    {
        "id": "shanjiao_haitan",
        "region": "神龙岛",
        "from": "山脚", "to": "海滩",
        "targets": ["海滩", "神龙岛海滩"],
        "hint": "起点：山脚",
        "steps": ["s", "s", "w"],
    },
    # ---- 桃源黑沼 / 钓鱼岛 / 扬州 ----
    {
        "id": "taoyuan_heizhao",
        "region": "桃源",
        "from": "入口", "to": "桃源黑沼",
        "targets": ["桃源黑沼"],
        "hint": "起点：桃源黑沼入口",
        "steps": ["s", "e", "n", "w", "s", "s", "w", "out"],
    },
    {
        "id": "diaoyudao_zhulin",
        "region": "钓鱼岛",
        "from": "入口", "to": "钓鱼岛竹林",
        "targets": ["钓鱼岛竹林"],
        "hint": "起点：钓鱼岛竹林入口",
        "steps": ["s", "e", "n", "nw"],
    },
    {
        "id": "yangzhou_tudimiao",
        "region": "扬州",
        "from": "扬州东门", "to": "土地庙",
        "targets": ["土地庙"],
        "hint": "起点：扬州东门",
        "steps": ["n", "e", "n", "w", "n", "e", "w", "n"],
    },
    # ---- 嘉兴海上 ----
    {
        "id": "jiaxing_haishang",
        "region": "嘉兴",
        "from": "嘉兴岸边", "to": "海上",
        "targets": ["嘉兴海上"],
        "hint": "起点：嘉兴岸边",
        "steps": ["jump jiang"],
    },
    {
        "id": "haishang_jiaxing",
        "region": "嘉兴",
        "from": "海上", "to": "嘉兴岸边",
        "targets": ["嘉兴"],
        "hint": "起点：海上",
        "steps": ["jump out"],
    },
    # ---- 回族部落 ----
    {
        "id": "huizu_buluo",
        "region": "回族部落",
        "from": "入口", "to": "回族部落",
        "targets": ["回族部落", "霍青桐"],
        "hint": "起点：入口；这是摸索出来的路线",
        "text": "出来直接 s;s（杀狼）",
        "steps": ["n", "e", "s", "e", "n", "n"],
    },
    # ---- 蛇谷荒地 ----
    {
        "id": "shegu_chushegu",
        "region": "蛇谷荒地",
        "from": "中央五出口房间", "to": "出蛇谷",
        "targets": ["出蛇谷", "蛇谷荒地"],
        "hint": "起点：蛇谷荒地中央（五个出口的房间）",
        "steps": ["w", "se", "ne"],
    },
    {
        "id": "shegu_moguaitan",
        "region": "蛇谷荒地",
        "from": "中央五出口房间", "to": "魔鬼滩",
        "targets": ["魔鬼滩"],
        "hint": "起点：蛇谷荒地中央（五个出口的房间）",
        "steps": ["w", "sw", "e"],
    },
    {
        "id": "shegu_shangou",
        "region": "蛇谷荒地",
        "from": "中央五出口房间", "to": "山沟",
        "targets": ["山沟"],
        "hint": "起点：蛇谷荒地中央（五个出口的房间）",
        "steps": ["w", "nw", "ne", "n", "n"],
    },
]

MAZE_GUIDES: list[dict] = [
    {
        "id": "taohuadao_taolin",
        "region": "桃花岛",
        "from": "入口", "to": "桃林出口",
        "targets": ["桃花岛桃林", "桃林"],
        "hint": "起点：桃花岛桃林",
        "text": "乱走走到出现「XX 方向好像是出去的」提示，走到该房间后对四个方向 look，即可看到出口（互博任务请看攻略，与此不同）",
    },
    {
        "id": "tidufu_huayuan",
        "region": "提督府",
        "from": "花园入口", "to": "后门",
        "targets": ["提督府花园", "提督府后门"],
        "hint": "起点：提督府花园",
        "text": "进入后每走一步先 look 东、西、南、北：描述「心里有种不祥的预感」是安全处可走；描述「有一股让人觉得很危险的气息」是陷阱，走过去会晕倒回提督府正门（若与守卫结仇且未杀死守卫，可能被砍死）。某方向描述含（拨花丛 bo huacong）就是出口，朝那方向走然后 bo huacong;enter 可见后门",
    },
    {
        "id": "miaoling_migong",
        "region": "苗疆苗岭",
        "from": "入口", "to": "出口",
        "targets": ["苗岭", "苗岭迷宫", "苗疆苗岭"],
        "hint": "起点：苗岭入口",
        "text": "昆明东门到底进苗岭方向为 e、eu、ed，走一步停一下慢走，出现 out 出口后 out；这个出口回昆明是 w、wu、wd，同样一步一停。南昌西门向西到底那个入口进苗岭：进 s、su、sd，回 n、nu、nd，一步一停，出现 out 后 out，通往青苗寨北边。带司南可能更容易，没有司南慢慢走一般也能出去",
    },
    {
        "id": "zhuangzu_laolin",
        "region": "壮族山寨",
        "from": "老林入口", "to": "出路",
        "targets": ["壮族山寨老林", "老林迷宫"],
        "hint": "起点：壮族山寨老林",
        "text": "方法1（带司南）：一直 nu;nd;n 直到出现「在你筋疲力尽之际，终于找到了出路(out)」；回来一直 eu;ed;e。方法2（没带司南）：w;#wa 2000;wd;#wa 2000;wu 循环",
    },
    {
        "id": "nanchang_laolin",
        "region": "南昌",
        "from": "老林入口", "to": "出路",
        "targets": ["南昌老林", "南昌迷宫"],
        "hint": "起点：南昌老林",
        "text": "每走一步有 5s busy，每隔 5s 朝一个方向走一次（如 w;#wa 5000;wd;#wa 5000;wu），大约十分钟后出现「在你筋疲力尽之际，终于找到了出路(out)」，此时 out 即可",
    },
    {
        "id": "shenlong_didao",
        "region": "神龙岛",
        "from": "山顶", "to": "地道内",
        "targets": ["神龙地道"],
        "hint": "起点：神龙岛山顶",
        "text": "在山顶上一直向里和北走到头，look light 直到出现不一样的描述，push light 进入",
    },
    {
        "id": "lingxiao_songlin",
        "region": "凌霄",
        "from": "松林入口", "to": "出路",
        "targets": ["凌霄松林"],
        "hint": "起点：凌霄松林",
        "text": "一直 sd，有机会走出来",
    },
]


def find_maze(query: str) -> list[dict]:
    """按搜索词匹配迷宫条目（先精确后包含）。返回命中条目列表。"""
    q = (query or "").strip()
    if not q:
        return []
    ql = q.lower()
    exact: list[dict] = []
    subs: list[dict] = []
    seen: set[str] = set()
    for e in (*MAZE_ROUTES, *MAZE_GUIDES):
        if e["id"] in seen:
            continue
        hit = False
        for t in e.get("targets", []):
            if str(t).lower() == ql:
                hit = True
                break
        if hit:
            exact.append(e)
            seen.add(e["id"])
            continue
        if len(ql) >= 2:
            for t in e.get("targets", []):
                tl = str(t).lower()
                if ql in tl or tl in ql:
                    hit = True
                    break
        if hit:
            subs.append(e)
            seen.add(e["id"])
    return exact or subs
