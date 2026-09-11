-- interior-keeper.lua: NPC que ATENDE um balcão — fica no lugar, vira-se para
-- o player e troca conversa. Versão de sala do `townsfolk.lua`: o raio de
-- passeio é zero de propósito. Numa sala de 6 m um passo de 4 m punha-o dentro
-- da parede, e o dono da loja não anda às voltas enquanto atende.
--
-- `banter` pode ser substituído por entidade (viber.state() é por entidade).
local GESTOS = { "talk", "yes", "no", "wave" }
local BANTER = {
  "“Sê bem-vindo. O que te traz por cá?”",
  "“Aqui dentro está-se melhor do que na estrada.”",
  "“Se precisas de algo, é só dizeres.”",
}

function on_update(dt)
  local st = viber.state()
  if not st.ready then
    st.ready = true
    st.gesture_t = 3 + math.random() * 4
    viber.set_interaction("Falar", "e", 3.0)
  end
  local has, px, py, pz = viber.player_position()
  if not has then return end
  local x, y, z = viber.position()
  local dist = math.sqrt((px - x) ^ 2 + (pz - z) ^ 2)
  if dist > 6.0 then return end
  viber.face_player()
  st.gesture_t = st.gesture_t - dt
  if st.gesture_t <= 0 then
    st.gesture_t = 7 + math.random() * 7
    viber.gesture(GESTOS[math.random(#GESTOS)])
  end
  if viber.interacted("e") then
    st.i = ((st.i or 0) % #BANTER) + 1
    viber.toast(BANTER[st.i])
    viber.gesture("talk")
  end
end
