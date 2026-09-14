-- merchant.lua: Bram, o mercador. [E] gira o player, fala com gesto e som;
-- fora de alcance a banter recomeça.
--
-- A LOJA vive em game/shop.lua (catálogo, preços e negociação em Lua) —
-- carregada como módulo e actualizada daqui: negociação acontece perto do
-- mercador, que é onde o seu script está vivo (LOD de IA).
local shop = viber.load("game/shop.lua")

local lines = {
  "“Ferro bom não se negocia barato, forasteiro.”",
  "“Precisa de uma lâmina? Chegou na hora certa.”",
  "“Dizem que há cristais nas ruínas ao leste...”",
}
function on_update(dt)
  local st = viber.state() -- POR ENTIDADE (no top-level era partilhado)
  if not st.ready then
    st.ready = true
    viber.set_interaction("Falar com Bram", "e", 3.5)
  end
  local dist = viber.distance_to_player()
  local near = dist ~= nil and dist <= 3.5
  if st.near and not near then
    st.i = nil -- saiu de alcance: banter recomeça
  end
  st.near = near
  if viber.interacted("e") then
    st.i = ((st.i or 0) % #lines) + 1
    viber.face_player()
    viber.gesture("talk")
    if st.i == 1 then
      viber.sound("shop_open") -- sino de boas-vindas na 1.ª fala
    end
    viber.toast(lines[st.i])
  end
  -- A loja (lista + cliques + ações buy/sell) é atualizada daqui.
  shop.update(dt)
end
