-- Regras do simple-rpg: vida do herói (via Health), cofre, XP, quests,
-- veneno e biomas. Estado plano em viber.game() para o save.
-- Os scripts chamam este módulo; a engine não conhece ouro nem missões.

local M = {}

local QUEST_FILES = {
  "quests/city_quests.json",
  "quests/dark_forest_quests.json",
  "quests/desert_quests.json",
  "quests/fantasy_quests.json",
  "quests/mountain_quests.json",
  "quests/story_quests.json",
  "quests/swamp_quests.json",
}

local KILL_XP = {
  boss = 300,
  ["bog-warden"] = 180,
  ["sand-worm"] = 160,
  witch = 140,
  shade = 22,
  ["bandit-archer"] = 20,
  bandit = 18,
  scorpion = 16,
  goblin = 14,
  wolf = 12,
  bogling = 10,
  slime = 6,
}
local STACK = 99
local RESOURCES = { gold = true, wood = true, stone = true }
-- Veneno gravado no save: empurra-se UMA vez para a engine por sessão.
-- (A flag em viber.game() sobreviveria ao load e nunca reaplicava.)
local venom_pushed = false

local function g()
  return viber.game()
end

local function num(key, default)
  local v = g()[key]
  if type(v) ~= "number" then
    return default or 0
  end
  return v
end

local function ensure_xp()
  local game = g()
  if type(game.xp) ~= "number" then game.xp = 0 end
  if type(game.xp_next) ~= "number" or game.xp_next < 1 then game.xp_next = 100 end
  if type(game.level) ~= "number" or game.level < 1 then game.level = 1 end
  if type(game.skill_points) ~= "number" then game.skill_points = 0 end
end

local defs
local function load_defs()
  if defs then
    return defs
  end
  defs = {}
  for _, path in ipairs(QUEST_FILES) do
    local ok, data = pcall(viber.load_json, path)
    if ok and type(data) == "table" then
      for _, def in ipairs(data) do
        if def.id then
          defs[#defs + 1] = def
        end
      end
    end
  end
  table.sort(defs, function(a, b) return a.id < b.id end)
  return defs
end

local function find_def(id)
  for _, def in ipairs(load_defs()) do
    if def.id == id then
      return def
    end
  end
  return nil
end

local function qkey(id)
  return "q." .. id
end

function M.quest_defs()
  return load_defs()
end

function M.quest_def(id)
  return find_def(id)
end

function M.quest_progress(id)
  local def = find_def(id)
  local need = 1
  if def and def.objective and def.objective.count then
    need = def.objective.count
  end
  return num("q." .. id .. ".n", 0), need
end

function M.quest_state(id)
  local state = g()[qkey(id)]
  if state == "active" or state == "ready" or state == "done" or state == "not_taken" then
    return state
  end
  return find_def(id) and "not_taken" or "unknown"
end

local function objective(def)
  return def.objective or {}
end

local function progress_of(id)
  return num(qkey(id) .. ".n", 0)
end

local function set_progress(id, n)
  g()[qkey(id) .. ".n"] = n
end

local function mark_ready(def)
  local obj = objective(def)
  local need = obj.count or 1
  if progress_of(def.id) >= need and M.quest_state(def.id) == "active" then
    g()[qkey(def.id)] = "ready"
    viber.toast("Objetivo completo: " .. (def.title or def.id))
    viber.sound("notification")
  end
end

local function repeatable(def)
  return def.npc == "notice_board"
end

local function clear_visits(def)
  for place in tostring((def.objective or {}).target or ""):gmatch("%S+") do
    g()[qkey(def.id) .. ".v." .. place] = nil
  end
end

function M.quest_accept(id)
  local def = find_def(id)
  if not def or M.quest_state(id) ~= "not_taken" then
    return false
  end
  g()[qkey(id)] = "active"
  set_progress(id, 0)
  clear_visits(def)
  viber.toast("Quest aceite: " .. (def.title or id))
  viber.sound("quest_accept")
  M.sync_collect()
  return true
end

local function grant_rewards(def)
  local rewards = def.rewards or {}
  if rewards.gold and rewards.gold > 0 then
    M.vault_add("gold", rewards.gold)
  end
  if rewards.xp and rewards.xp > 0 then
    M.add_xp(rewards.xp)
  end
  if type(rewards.items) == "table" then
    for _, item in ipairs(rewards.items) do
      local name, count = tostring(item):match("^([^:]+):?(%d*)$")
      if name and name ~= "" then
        M.item_add(name, tonumber(count) or 1)
      end
    end
  end
end

function M.quest_turn_in(id)
  if M.quest_state(id) ~= "ready" then
    return false
  end
  local def = find_def(id)
  if not def then
    return false
  end
  local obj = objective(def)
  if obj.type == "collect" then
    local need = obj.count or 1
    if not M.vault_take(obj.target or "", need) then
      g()[qkey(id)] = "active"
      viber.toast("Já não tens o que a missão pede.")
      return false
    end
  end
  if repeatable(def) then
    g()[qkey(id)] = "not_taken"
    set_progress(id, 0)
  else
    g()[qkey(id)] = "done"
  end
  grant_rewards(def)
  local gold = (def.rewards or {}).gold or 0
  viber.toast("Quest entregue: " .. (def.title or id) .. (gold > 0 and (" (+" .. gold .. " ouro)") or ""))
  viber.sound("quest_complete")
  return true
end

-- bandit-archer conta como bandit. Substring ao contrário ("a" dentro de
-- "bandit", ou "boss" dentro de um nome qualquer) contava abates a mais.
local function kill_matches(kind, target)
  if kind == "" or target == "" then
    return false
  end
  if kind == target then
    return true
  end
  return kind:sub(1, #target + 1) == target .. "-"
end

function M.report_kill(kind)
  if not kind or kind == "" then
    return
  end
  for _, def in ipairs(load_defs()) do
    if M.quest_state(def.id) == "active" then
      local obj = objective(def)
      if obj.type == "kill" and kill_matches(kind, obj.target or "") then
        local need = obj.count or 1
        set_progress(def.id, math.min(need, progress_of(def.id) + 1))
        mark_ready(def)
      end
    end
  end
end

function M.report_visit(place)
  if not place or place == "" then
    return
  end
  for _, def in ipairs(load_defs()) do
    if M.quest_state(def.id) == "active" then
      local obj = objective(def)
      if obj.type == "visit" then
        local listed = false
        for token in tostring(obj.target or ""):gmatch("%S+") do
          if token == place then
            listed = true
            break
          end
        end
        local mark = qkey(def.id) .. ".v." .. place
        if listed and g()[mark] ~= true then
          g()[mark] = true
          local n = 0
          for token in tostring(obj.target or ""):gmatch("%S+") do
            if g()[qkey(def.id) .. ".v." .. token] == true then
              n = n + 1
            end
          end
          set_progress(def.id, n)
          mark_ready(def)
          viber.toast("Marco visto: " .. place)
        end
      end
    end
  end
end

function M.scan_visits()
  local has, px, _, pz = viber.player_position()
  if not has then
    return
  end
  for _, def in ipairs(load_defs()) do
    if M.quest_state(def.id) == "active" then
      local obj = objective(def)
      if obj.type == "visit" then
        local radius = obj.radius or 12
        for token in tostring(obj.target or ""):gmatch("%S+") do
          if g()[qkey(def.id) .. ".v." .. token] ~= true then
            local id = viber.find(token)
            if id then
              local pok, x, _, z = viber.entity_position(id)
              if pok then
                local dx, dz = x - px, z - pz
                if dx * dx + dz * dz <= radius * radius then
                  M.report_visit(token)
                end
              end
            end
          end
        end
      end
    end
  end
end

function M.report_collect(item, amount)
  M.item_add(item, amount or 1)
  M.sync_collect()
end

function M.sync_collect()
  for _, def in ipairs(load_defs()) do
    local state = M.quest_state(def.id)
    if state == "active" or state == "ready" then
      local obj = objective(def)
      if obj.type == "collect" then
        local need = obj.count or 1
        local have = M.vault_get(obj.target or "")
        set_progress(def.id, math.min(have, need))
        if have >= need then
          mark_ready(def)
        elseif state == "ready" then
          g()[qkey(def.id)] = "active"
        end
      end
    end
  end
end

function M.vault_get(kind)
  return num("item." .. kind, 0)
end

local function mirror_add(kind, amount)
  if amount <= 0 then
    return
  end
  if RESOURCES[kind] then
    viber.vault_add(kind, amount)
  else
    viber.item_add(kind, amount)
  end
end

local function mirror_take(kind, amount)
  if amount > 0 then
    viber.vault_take(kind, amount)
  end
end

function M.pull_engine()
  if g().eco_pulled == true then
    return
  end
  g().eco_pulled = true
  local tracked = {
    "gold", "wood", "stone", "dark-wood", "bog-moss", "nota",
    "potion", "antidote", "bomb", "korok-seed",
  }
  for _, kind in ipairs(tracked) do
    local n = RESOURCES[kind] and (viber.vault_get(kind) or 0) or (viber.item_count(kind) or 0)
    if n > 0 then
      g()["item." .. kind] = n
    end
  end
end

function M.vault_add(kind, amount, mirror)
  amount = amount or 1
  if type(amount) ~= "number" or amount ~= amount or amount <= 0 or not kind or kind == "" then
    return 0
  end
  local key = "item." .. kind
  local have = num(key, 0)
  local cap = RESOURCES[kind] and 1000000 or STACK
  local nextv = math.min(cap, have + amount)
  local added = nextv - have
  if added <= 0 then
    return 0
  end
  g()[key] = nextv
  if mirror ~= false then
    mirror_add(kind, added)
  end
  M.sync_collect()
  return added
end

function M.vault_take(kind, amount)
  amount = amount or 1
  if type(amount) ~= "number" or amount ~= amount or amount <= 0 then
    return false
  end
  local key = "item." .. kind
  local have = num(key, 0)
  if have < amount then
    return false
  end
  g()[key] = have - amount
  mirror_take(kind, amount)
  M.sync_collect()
  return true
end

function M.item_add(id, amount)
  return M.vault_add(id, amount or 1)
end

function M.item_count(id)
  return M.vault_get(id)
end

function M.add_xp(gain)
  gain = gain or 0
  if gain <= 0 then
    return
  end
  ensure_xp()
  local game = g()
  game.xp = game.xp + gain
  local leveled = false
  while game.xp >= game.xp_next do
    game.xp = game.xp - game.xp_next
    game.xp_next = math.max(1, math.ceil(game.xp_next * 1.5))
    game.level = game.level + 1
    game.skill_points = game.skill_points + 1
    leveled = true
  end
  if leveled then
    viber.toast("NÍVEL " .. game.level)
    viber.sound("levelup")
    local ok, x, y, z = viber.player_position()
    if ok then
      viber.burst("magic", x, y + 1, z, 16)
    end
  end
end

function M.add_skill_points(n)
  n = n or 0
  if n <= 0 then
    return
  end
  ensure_xp()
  g().skill_points = num("skill_points", 0) + n
end

function M.reveal_biome(name)
  if name and name ~= "" then
    g()["biome." .. name] = true
  end
end

function M.biome_revealed(name)
  return g()["biome." .. name] == true
end

local function region_of(x, z)
  if math.abs(x) <= 120 and math.abs(z) <= 120 then
    return 0
  end
  if math.abs(x) >= math.abs(z) then
    return x >= 0 and 3 or 4
  end
  return z >= 0 and 1 or 2
end

function M.alive_in_region(idx)
  local list = viber.hostiles(4000) or {}
  local n = 0
  for _, h in ipairs(list) do
    if region_of(h.x or 0, h.z or 0) == idx and (h.hp or 0) > 0 then
      n = n + 1
    end
  end
  return n
end

function M.apply_status(kind, secs)
  if kind == "venom" then
    local dur = math.max(num("venom", 0), secs or 0)
    g().venom = dur
    viber.apply_status("venom", dur)
  end
end

function M.status_clear(kind)
  if kind == nil or kind == "venom" then
    g().venom = 0
    viber.status_clear("venom")
  end
end

function M.poisoned()
  return num("venom", 0) > 0
end

local function kill_xp(kind)
  return KILL_XP[kind] or 15
end

function M.damage_player(amount)
  if num("iframes", 0) > 0 then
    return
  end
  local id = viber.player_id()
  if not id then
    return
  end
  viber.entity_damage(amount, id)
  viber.damage_number("-" .. math.floor(amount), { color = "#ff6b6b" })
  viber.sound("hurt")
  viber.shake(0.18)
  g().iframes = 0.35
end

function M.heal_player(amount)
  local id = viber.player_id()
  if id then
    viber.entity_heal(amount, id)
  end
end

function M.grant_iframes(secs)
  g().iframes = math.max(num("iframes", 0), secs or 0)
end

function M.tick(dt)
  M.pull_engine()
  local id = viber.player_id()
  local ok = select(1, viber.player_hp())
  if id and not ok then
    viber.entity_set_max_hp(100, id)
  end
  local game = g()
  game.iframes = math.max(0, num("iframes", 0) - dt)
  if num("venom", 0) > 0 then
    game.venom = math.max(0, game.venom - dt)
  end
  -- O tick de dano do veneno é da engine. Aqui só se re-empurra a duração
  -- gravada e se espelha o tempo que falta, sem segundo golpe por segundo.
  if not venom_pushed and num("venom", 0) > 0 then
    venom_pushed = true
    viber.apply_status("venom", g().venom)
  end
  for _, ev in ipairs(viber.events()) do
    if ev.type == "kill" and ev.name then
      M.add_xp(kill_xp(ev.name))
      M.report_kill(ev.name)
      viber.sound("enemy_death")
    elseif ev.type == "collect" and ev.item then
      -- A engine já depositou no cofre dela. Espelhar de novo duplicava a lenha.
      M.vault_add(ev.item, ev.amount or 1, false)
      M.add_xp(8)
    end
  end
  M.scan_visits()
  M.sync_collect()
  M.publish()
end

function M.publish()
  ensure_xp()
  local game = g()
  local ok, hp, max_hp = viber.player_hp()
  if not ok then
    hp, max_hp = 100, 100
  end
  local frac = max_hp > 0 and math.max(0, math.min(1, hp / max_hp)) or 0
  viber.ui.set("health", frac)
  viber.ui.set("health.text", string.format("%d/%d", math.floor(hp + 0.5), math.floor(max_hp + 0.5)))
  viber.ui.set("health.value", hp)
  viber.ui.set("health.low", frac <= 0.3)
  viber.ui.set("status.venom", num("venom", 0) > 0)
  viber.ui.set("xp", game.xp / game.xp_next)
  viber.ui.set("xp.text", string.format("%d/%d", math.floor(game.xp), math.floor(game.xp_next)))
  viber.ui.set("level", game.level)
  viber.ui.set("level.text", tostring(game.level))
  viber.ui.set("talent.points", game.skill_points)
  viber.ui.set("talent.ready", game.skill_points > 0)
  viber.ui.set("gold", M.vault_get("gold"))
  viber.ui.set("wood", M.vault_get("wood"))
  viber.ui.set("stone", M.vault_get("stone"))
  viber.ui.set("korok-seed", M.vault_get("korok-seed"))
  viber.ui.set("potion", M.vault_get("potion"))
  viber.ui.set("antidote", M.vault_get("antidote"))
  viber.ui.set("bomb", M.vault_get("bomb"))
  viber.ui.set("potion.empty", M.vault_get("potion") < 1)
  viber.ui.set("antidote.empty", M.vault_get("antidote") < 1)
  viber.ui.set("bomb.empty", M.vault_get("bomb") < 1)
  local title, progress, text = "", 0, ""
  local best, best_score
  for _, def in ipairs(load_defs()) do
    local state = M.quest_state(def.id)
    if state == "active" or state == "ready" then
      local obj = objective(def)
      local need = obj.count or 1
      local have = progress_of(def.id)
      local score = (state == "ready" and 2 or 0) + (need > 0 and have / need or 0)
      if not best_score or score > best_score then
        best, best_score = def, score
        title = def.title or def.id
        progress = need > 0 and math.min(1, have / need) or 0
        text = string.format("%d/%d", have, need)
        if state == "ready" then
          text = "pronta"
          progress = 1
        end
      end
    end
  end
  viber.ui.set("quest.title", title)
  viber.ui.set("quest", progress)
  viber.ui.set("quest.text", text)
  viber.ui.set("quest.active", title ~= "")
  viber.ui.set("abilities.active", true)
  viber.ui.set("vitals.active", true)
end

return M
