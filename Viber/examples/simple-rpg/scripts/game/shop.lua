-- game/shop.lua: a LOJA é do JOGO — catálogo, preços e negociação em Lua.
--
-- A engine só publica a ação: os botões da tab Loja fazem
-- `viber.ui.action("buy"/"sell", item)` e chegam aqui via `viber.events()`
-- como {type="ui_action", name=…, arg=…}. Este módulo reclama a fonte da
-- lista `shop` (`viber.ui.list` — a engine deixa de a alimentar) e reclama
-- as ações (`viber.own_action` — o catálogo nativo cala). Sem este módulo,
-- a loja nativa da engine continua a funcionar (compat).
--
-- Uso (ver merchant.lua): `local shop = viber.load("game/shop.lua")` no
-- top-level e `shop.update(dt)` dentro do `on_update`.

-- Catálogo do mercador: buy = preço de compra (nil = não se vende ao herói),
-- sell = preço de venda (nil = não se compra para si). Preços em ouro.
local CATALOG = {
  potion    = { label = "Comprar poção",       buy = 25 },
  antidote  = { label = "Comprar antídoto",    buy = 20 },
  bomb      = { label = "Comprar bomba",       buy = 40 },
  wood      = { label = "Vender madeira",      sell = 3 },
  stone     = { label = "Vender pedra",        sell = 5 },
  -- Loot de quest sem consumidor: SÓ venda, preço por raridade acima do
  -- feito bruto (madeira 3 g / pedra 5 g).
  wolf_pelt      = { label = "Vender pele de lobo",        sell = 15 },
  cactus_fiber   = { label = "Vender fibra de cacto",      sell = 15 },
  silk_cloth     = { label = "Vender seda",                sell = 20 },
  moss_potion    = { label = "Vender poção de musgo",      sell = 20 },
  iron_axe       = { label = "Vender machado de ferro",    sell = 30 },
  nature_amulet  = { label = "Vender amuleto da natureza", sell = 30 },
  blessed_rod    = { label = "Vender vara abençoada",      sell = 40 },
  ancient_relic  = { label = "Vender relíquia ancestral",  sell = 50 },
}

local M = {}

local function rows()
  local out = {}
  local gold = viber.vault_get("gold")
  for id, entry in pairs(CATALOG) do
    local selling = entry.sell ~= nil
    local stock = viber.vault_get(id)
    -- status alimenta a classe CSS da linha ({status} no template).
    local status = "ready"
    if selling then
      if (stock or 0) == 0 then status = "locked" end
    elseif gold < entry.buy then
      status = "locked"
    end
    out[#out + 1] = {
      id = id,
      label = entry.label,
      qty = "1",
      price = selling and ("+" .. entry.sell) or tostring(entry.buy),
      status = status,
    }
  end
  table.sort(out, function(a, b) return a.label < b.label end)
  return out
end

local function refresh()
  viber.ui.list("shop", rows())
end

-- top-level do módulo (corre 1× por mundo): reclama lista + ações.
viber.own_action("buy")
viber.own_action("sell")
refresh()

local function trade(action, id)
  local entry = CATALOG[id]
  if entry == nil then return end
  if action == "buy" then
    local price = entry.buy
    if price == nil then
      viber.toast("Não vendo isso.")
      viber.sound("error")
      return
    end
    if viber.vault_get("gold") < price then
      viber.toast(entry.label .. " custa " .. price .. " de ouro — não chega.")
      viber.sound("error")
      return
    end
    viber.vault_take("gold", price)
    viber.item_add(id, 1)
    viber.sound("buy")
    viber.toast("Comprado " .. entry.label:gsub("^Comprar ", "") .. ".")
  elseif action == "sell" then
    local price = entry.sell
    if price == nil or viber.vault_get(id) == 0 then
      viber.toast("Sem isso para vender.")
      viber.sound("error")
      return
    end
    viber.vault_take(id, 1)
    viber.vault_add("gold", price)
    viber.sound("coin")
    viber.toast("Vendido por " .. price .. " de ouro.")
  end
  refresh()
end

function M.update(dt)
  -- Os CLIQUES das linhas continuam a ser roteados pelo ui/menu.lua
  -- (`shop-{id}` → `viber.ui.action`) — assim a loja funciona até longe do
  -- mercador (sem dono, o handler nativo da engine responde; perto dele,
  -- este módulo é que negocia). Só as AÇÕES são nossas:
  for _, ev in ipairs(viber.events()) do
    if ev.type == "ui_action" and (ev.name == "buy" or ev.name == "sell") then
      trade(ev.name, ev.arg)
    end
  end
end

return M
