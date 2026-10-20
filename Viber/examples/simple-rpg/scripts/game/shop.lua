local rules = viber.load("lib/rules.lua")
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
-- top-level e `shop.update(dt)` dentro do `on_update`. O mercador leva
-- `tag="always-active"`: sem isso o LOD de 45 m congelava o script, os
-- cliques buy/sell acumulavam na fila e descarregavam todos ao voltar
-- (compras em rajada) — e a lista ficava no ouro do frame em que a loja
-- abriu, porque `refresh` lia o snapshot ANTES dos comandos da compra.

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
  local gold = rules.vault_get("gold")
  for id, entry in pairs(CATALOG) do
    local selling = entry.sell ~= nil
    local stock = rules.vault_get(id)
    -- status alimenta a classe CSS da linha ({status} no template).
    local status = "ready"
    if selling then
      if (stock or 0) == 0 then status = "locked" end
    elseif gold < entry.buy then
      status = "locked"
    end
    local label = entry.label
    if selling then
      label = label .. " ×" .. tostring(stock or 0)
    end
    out[#out + 1] = {
      id = id,
      label = label,
      qty = "1",
      price = selling and ("+" .. entry.sell) or tostring(entry.buy),
      status = status,
    }
  end
  table.sort(out, function(a, b) return a.label < b.label end)
  return out
end

-- A lista só é reescrita quando o conteúdo muda: `viber.ui.list` sobe a
-- versão do repetidor e reconstrói as linhas (perde o scroll) se for
-- chamada todos os frames com a mesma tabela.
local last_sig = ""

local function signature(list)
  local parts = {}
  for i, row in ipairs(list) do
    parts[i] = row.id .. "\0" .. row.status .. "\0" .. row.price .. "\0" .. row.label
  end
  return table.concat(parts, "\1")
end

local function refresh()
  local list = rows()
  local sig = signature(list)
  if sig == last_sig then
    return
  end
  last_sig = sig
  viber.ui.list("shop", list)
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
    if rules.vault_get("gold") < price then
      viber.toast(entry.label .. " custa " .. price .. " de ouro — não chega.")
      viber.sound("error")
      return
    end
    if rules.vault_get(id) >= 99 then
      viber.toast("Já tens o máximo disso (99).")
      viber.sound("error")
      return
    end
    rules.vault_take("gold", price)
    local got = rules.item_add(id, 1)
    if not got or got < 1 then
      rules.vault_add("gold", price)
      viber.toast("Não coube na mochila.")
      viber.sound("error")
      return
    end
    viber.sound("buy")
    viber.toast("Comprado " .. entry.label:gsub("^Comprar ", "") .. ".")
  elseif action == "sell" then
    local price = entry.sell
    if price == nil or rules.vault_get(id) == 0 then
      viber.toast("Sem isso para vender.")
      viber.sound("error")
      return
    end
    rules.vault_take(id, 1)
    rules.vault_add("gold", price)
    viber.sound("coin")
    viber.toast("Vendido por " .. price .. " de ouro.")
  end
  refresh()
end

function M.update(dt)
  -- Snapshot deste frame (já inclui as compras do frame anterior). Sem
  -- isto a linha ficava "pronta" depois de o ouro acabar, até ao clique
  -- seguinte — e o ouro de um abate nunca destrancava uma compra.
  refresh()
  -- Os CLIQUES das linhas são roteados pelo ui/menu.lua
  -- (`shop-{id}` → `viber.ui.action`). Várias entidades `merchant.lua` na
  -- mesma cena partilham ESTA fila (uma por path): a primeira drena, a
  -- segunda vê-a vazia — uma compra não cobra duas vezes.
  for _, ev in ipairs(viber.events()) do
    if ev.type == "ui_action" and (ev.name == "buy" or ev.name == "sell") then
      trade(ev.name, ev.arg)
    end
  end
end

return M
