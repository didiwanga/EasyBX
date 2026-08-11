-- Didiwang 练功辅助（日月神教新手）v4
-- timeout: 3600000
-- 目标：把基本功夫 + 特殊功夫都练到接近技能上限
-- 用法：查看→脚本→Lua脚本→导入→运行
-- 说明：每 5 秒发一次练功命令；每 15 轮打印一次技能进度快照，方便确认在练。
-- 安全：只发练功/打坐/吃喝，不移动不打怪，可随时停止。

local TARGET_GAP = 3
local CYCLE_SEC = 5

local BASE = {"force", "parry", "dodge", "blade", "strike", "sword", "claw"}
local SPECIAL = {
  {"riyue-shengong", "xiulian"},
  {"riyue-dao", "practice"},
  {"riyue-jian", "practice"},
  {"hanbing-zhang", "practice"},
  {"yinfeng-zhao", "practice"},
  {"feitian-shenfa", "practice"},
}

local tick = 0
local last_act = ""
local last_skill = ""
local line_count = 0

local function log(msg)
  out("[练功] " .. msg)
end

-- 捕获 skills 输出到 var：cap_<en> / max_<en>
local printed = 0
bus.subscribe("net.text_display", function(p)
  local line = p.line or ""
  line_count = line_count + 1
  -- 采样：前 15 行打印原文（前 40 字符，含频道前缀），看收到的是什么
  if printed < 15 then
    printed = printed + 1
    out("[行" .. printed .. "] " .. string.sub(line, 1, 40))
  end
  local en, cur, cap = string.match(line, "[(（]?%s*([%a_%-]+)%s*[)）]?%s*[^%d]*([%d%.]+)%s*[/／]%s*(%d+)")
  if en and cur and cap then
    var.set("cap_" .. en, tonumber(cur))
    var.set("max_" .. en, tonumber(cap))
  end
end)

local function skill_ready(en)
  local cap = tonumber(var.get("max_" .. en, 0)) or 0
  if cap <= 0 then return false end
  local cur = tonumber(var.get("cap_" .. en, 0)) or 0
  return cur >= cap - TARGET_GAP
end

local function act(msg)
  if msg ~= last_act then
    last_act = msg
    log(msg)
  end
end

local function pick()
  for _, en in ipairs(BASE) do
    if not skill_ready(en) then return en, "lian" end
  end
  for _, spec in ipairs(SPECIAL) do
    if not skill_ready(spec[1]) then return spec[1], spec[2] end
  end
  return nil
end

local function heal_if_needed()
  local qi = tonumber(state.get("qi", 0)) or 0
  local mq = tonumber(state.get("max_qi", 0)) or 0
  local jing = tonumber(state.get("jing", 0)) or 0
  local mj = tonumber(state.get("max_jing", 0)) or 0
  local food = tonumber(state.get("food", 300)) or 300
  local water = tonumber(state.get("water", 300)) or 300
  if mq > 0 and qi > 0 and qi < mq * 0.6 then
    act("气血低(" .. qi .. "/" .. mq .. ")，疗伤")
    send("exert heal")
    return true
  end
  if mj > 0 and jing > 0 and jing < mj * 0.6 then
    act("精神低(" .. jing .. "/" .. mj .. ")，恢复")
    send("exert recover")
    return true
  end
  if food < 80 then send("eat liang") end
  if water < 80 then send("drink jiudai") end
  return false
end

local function dazuo_if_needed()
  local neili = tonumber(state.get("neili", 0)) or 0
  local max_neili = tonumber(state.get("max_neili", 0)) or 0
  if max_neili > 0 and neili < max_neili * 0.7 then
    act("内力低(" .. neili .. "/" .. max_neili .. ")，打坐")
    send("dazuo 200")
    return true
  end
  return false
end

-- 打印技能进度快照
local function report()
  local parts = {}
  for _, en in ipairs(BASE) do
    local cur = tonumber(var.get("cap_" .. en, -1)) or -1
    local cap = tonumber(var.get("max_" .. en, 0)) or 0
    if cur >= 0 then
      table.insert(parts, en .. "=" .. cur .. "/" .. cap)
    end
  end
  for _, spec in ipairs(SPECIAL) do
    local cur = tonumber(var.get("cap_" .. spec[1], -1)) or -1
    if cur >= 0 then
      table.insert(parts, spec[1] .. "=" .. cur)
    end
  end
  log("技能进度: " .. table.concat(parts, "  ") .. "  [收到行数 " .. line_count .. "]")
end

local function train()
  local en, method = pick()
  if not en then
    act("全部技能已练满，可以去做门忠/慕容任务了。")
    return
  end
  local msg = "练 " .. en .. "（" .. method .. "）"
  if msg ~= last_skill then
    last_skill = msg
    log(msg)
  end
  send(method .. " " .. en .. " 10")
end

local function step()
  tick = tick + 1
  if heal_if_needed() then return end
  if dazuo_if_needed() then return end
  train()
  -- 每 5 轮（约25秒）重发 hp/skills，保持技能数据新鲜
  if tick % 5 == 0 then
    send("hp")
    send("skills")
  end
  if tick % 20 == 0 then
    report()
  end
end

log("练功辅助启动（每" .. CYCLE_SEC .. "秒一轮，每轮发一次练功命令）")
send("hp")
send("skills")

while true do
  bus.poll()
  step()
  sleep(CYCLE_SEC)
end
