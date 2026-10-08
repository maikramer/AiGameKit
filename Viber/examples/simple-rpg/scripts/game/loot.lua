-- game/loot.lua: DESPOJOS dos abates — o jogo decide, em Lua, o que cai.
--
-- A engine já dá XP, cadáver e o progresso de quest no abate; o ouro vinha
-- só de quests e da venda de recursos, pelo que caçar não pagava nada.
-- Este controller ouve os eventos `kill` (`viber.events()`, fila própria)
-- e paga no sítio do cadáver: ouro por tipo de criatura (intervalo) e uma
-- CHANCE de consumível — poção para toda a gente, antídoto sobretudo dos
-- venenosos. Os chefes pagam uma bolsa grande e sempre poções.
--
-- Host: `world/gameplay.xml` (`tag="always-active"` — abates longe do raio
-- de 45 m de ativação continuam a pagar).

-- kind (nome do script da criatura) → { ouro mín, ouro máx, p(poção), p(antídoto) }
local TABLE = {
  slime      = { 1, 2, 0.04, 0.00 },
  wolf       = { 2, 4, 0.06, 0.00 },
  bogling    = { 2, 4, 0.06, 0.08 },
  goblin     = { 3, 6, 0.08, 0.00 },
  scorpion   = { 3, 5, 0.05, 0.18 },
  shade      = { 4, 7, 0.10, 0.00 },
  bandit     = { 5, 9, 0.10, 0.05 },
  -- Chefes: bolsa + 2 poções garantidas (p > 1 = garantido, parte inteira).
  witch        = { 60, 90, 2.0, 1.0 },
  ["sand-worm"]  = { 70, 100, 2.0, 1.0 },
  ["bog-warden"] = { 80, 110, 2.0, 2.0 },
  boss         = { 150, 200, 3.0, 1.0 },
}

local GOLD_COLOR = "#ffd35a"
local ITEM_COLOR = "#8fe3a0"

-- Rolagem: `p` ≥ 1 dá floor(p) garantidos + a fração como chance extra.
local function roll_count(p)
  local n = math.floor(p)
  if math.random() < p - n then n = n + 1 end
  return n
end

local function pay(kind, id)
  local entry = TABLE[kind]
  if not entry then return end
  local ok, x, y, z = viber.entity_position(id)
  if not ok then
    local _, px, py, pz = viber.player_position()
    x, y, z = px, py, pz
  end
  local gold = math.random(entry[1], entry[2])
  viber.vault_add("gold", gold)
  viber.damage_number("+" .. gold .. " ouro", { color = GOLD_COLOR, x = x, y = y + 1.6, z = z })
  viber.burst("sparkle", x, y + 0.8, z, 10)
  viber.sound("coin")
  local potions, antidotes = roll_count(entry[3]), roll_count(entry[4])
  if potions > 0 then
    viber.item_add("potion", potions)
    viber.damage_number("+" .. potions .. " poção", { color = ITEM_COLOR, x = x, y = y + 2.2, z = z })
  end
  if antidotes > 0 then
    viber.item_add("antidote", antidotes)
    viber.damage_number("+" .. antidotes .. " antídoto", { color = ITEM_COLOR, x = x, y = y + 2.6, z = z })
  end
end

function on_update(dt)
  for _, ev in ipairs(viber.events()) do
    if ev.type == "kill" then
      pay(ev.name, ev.entity)
    end
  end
end
