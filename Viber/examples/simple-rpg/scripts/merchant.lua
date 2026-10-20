-- merchant.lua: Bram, o mercador. [E] gira o player, fala com gesto e som;
-- fora de alcance a banter recomeça.
--
-- A LOJA vive em game/shop.lua (catálogo, preços e negociação em Lua).
-- Este script tem de correr SEMPRE (`tag="always-active"`): o LOD de 45 m
-- engolia os cliques buy/sell e despejava-os todos ao regressar à banca.
local shop = viber.load("game/shop.lua")

--@export prompt: string = "Falar com Bram"     -- texto do prompt de interação
--@export range: number(0.5..10) = 3.5          -- alcance do [E] (m)
--@export lines: string[] = "“Ferro bom não se negocia barato, forasteiro.”" | "“Precisa de uma lâmina? Chegou na hora certa.”" | "“Dizem que há cristais nas ruínas ao leste...”"
function on_update(dt)
  local st = viber.state() -- POR ENTIDADE (no top-level era partilhado)
  if not st.ready then
    st.ready = true
    viber.set_interaction(viber.prop("prompt"), "e", viber.prop("range"))
  end
  local dist = viber.distance_to_player()
  local near = dist ~= nil and dist <= viber.prop("range")
  if st.near and not near then
    st.i = nil -- saiu de alcance: banter recomeça
  end
  st.near = near
  if viber.interacted("e") then
    local lines = viber.prop("lines")
    st.i = ((st.i or 0) % math.max(#lines, 1)) + 1
    viber.face_player()
    viber.gesture("talk")
    if st.i == 1 then
      viber.sound("shop_open") -- sino de boas-vindas na 1.ª fala
    end
    if lines[st.i] then
      viber.toast(lines[st.i])
    end
    -- Falar com o Bram abre a banca: o diário no separador Loja (antes a
    -- loja só se achava sabendo que existia um separador no [Q]).
    viber.ui.open("menu", true)
    viber.ui.select_tab("menu", "shop")
  end
  -- A loja (lista + cliques + ações buy/sell) é atualizada daqui.
  shop.update(dt)
end
