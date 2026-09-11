-- interior-folk.lua: gente dentro de casa — passeia DOIS passos, para, olha
-- para o player e gesticula; [E] troca conversa. Versão de sala do
-- `townsfolk.lua`, que foi escrito para a rua (raio de 4 m): aqui o raio é 1.6
-- e a velocidade 0.6, senão o NPC atravessa a parede e o anti-stuck passa a
-- vida a recalcular caminho.
local SPEED = 0.6
local RADIUS = 1.6
local GESTOS = { "wave", "talk", "yes", "no" }
local BANTER = {
  "“Bom dia. Não te vi entrar.”",
  "“Está frio lá fora, não está?”",
  "“Já viste o quadro de avisos? Há sempre trabalho.”",
}

function on_update(dt)
  local st = viber.state()
  if not st.ready then
    st.ready = true
    st.gesture_t = 3 + math.random() * 4
    st.tx, st.tz = viber.wander_target(RADIUS)
    viber.set_interaction("Falar", "e", 3.0)
  end
  local has, px, py, pz = viber.player_position()
  if not has then return end
  local x, y, z = viber.position()
  local dist = math.sqrt((px - x) ^ 2 + (pz - z) ^ 2)
  if dist < 3.0 then
    viber.face_player()
    st.gesture_t = st.gesture_t - dt
    if st.gesture_t <= 0 then
      st.gesture_t = 7 + math.random() * 8
      viber.gesture(GESTOS[math.random(#GESTOS)])
    end
    if viber.interacted("e") then
      st.i = ((st.i or 0) % #BANTER) + 1
      viber.toast(BANTER[st.i])
      viber.gesture("talk")
    end
    return
  end
  local td = math.sqrt((st.tx - x) ^ 2 + (st.tz - z) ^ 2)
  if td < 0.35 then
    st.tx, st.tz = viber.wander_target(RADIUS)
    return
  end
  if st.td ~= nil and td >= st.td then
    st.stuck = (st.stuck or 0) + dt
    if st.stuck > 3.0 then
      st.tx, st.tz = viber.wander_target(RADIUS)
      st.stuck, st.td = 0, nil
      return
    end
  else
    st.stuck = 0
  end
  st.td = td
  viber.move_towards(st.tx, st.tz, SPEED)
end
