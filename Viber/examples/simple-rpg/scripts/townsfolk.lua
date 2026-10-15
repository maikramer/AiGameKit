-- townsfolk.lua: o NPC civil do mundo. Passeia perto de casa; perto do player
-- para, vira-se e gesticula de vez em quando; [E] troca conversa.
--
-- Um só script para a rua, as salas e os balcões: o que muda é DADO de cada
-- instância (`prop-*` no XML, Inspector › Script no editor, `viber scene set`):
--   * rua (default): passeio de 4 m a 1.1 m/s;
--   * dentro de casa: `prop-radius="1.6" prop-speed="0.6"` — com o passeio de
--     rua o NPC atravessava a parede;
--   * ao balcão: `prop-radius="0"` — fica no lugar e atende.
--@export speed: number(0..4) = 1.1                 -- velocidade a passear (m/s)
--@export radius: number(0..20) = 4                 -- raio do passeio à volta de casa (m); 0 = fica no lugar
--@export talk_range: number(0.5..15) = 4.5        -- abaixo desta distância para e vira-se para o player (m)
--@export interact_range: number(0.5..10) = 3.5    -- alcance do [E] (m)
--@export prompt: string = "Falar"                  -- texto do prompt de interação
--@export banter: string[] = "“Bom dia para uma caminhada, não achas?”" | "“Dizem que há ferro novo no mercado.”" | "“Cuidado com os lobos a norte.”"
--@export gestures: string[] = wave | talk | yes | no   -- gestos ao acaso enquanto o player está perto
--@export notice: string = ""                      -- fala quando reage a um sinal (`notice`); "" = só se vira
--@export notice_range: number(0..60) = 18         -- só reage com o player a menos disto (m)

-- Alvo de sinais: `connect-opened="@discordia:notice"` num baú faz os
-- moradores do grupo reagirem (vira-se para o player, abana a cabeça e,
-- com `prop-notice`, comenta). Os argumentos do sinal não interessam aqui.
function notice(...)
  local has, px, _, pz = viber.player_position()
  if not has then return end
  local x, _, z = viber.position()
  if math.sqrt((px - x) ^ 2 + (pz - z) ^ 2) > viber.prop("notice_range") then return end
  viber.face_player()
  viber.gesture("no")
  local line = viber.prop("notice")
  if line ~= "" then viber.toast(line) end
end

function on_update(dt)
  local st = viber.state() -- POR ENTIDADE (o top-level é partilhado)
  if not st.ready then
    st.ready = true
    st.gesture_t = 3 + math.random() * 4 -- 1.º gesto cedo
    viber.set_interaction(viber.prop("prompt"), "e", viber.prop("interact_range"))
  end
  local has, px, py, pz = viber.player_position()
  if not has then return end
  local x, y, z = viber.position()
  local dist = math.sqrt((px - x) ^ 2 + (pz - z) ^ 2)
  if dist < viber.prop("talk_range") then
    viber.face_player()
    st.gesture_t = st.gesture_t - dt
    if st.gesture_t <= 0 then
      st.gesture_t = 7 + math.random() * 8 -- 7–15 s
      local gestures = viber.prop("gestures")
      if #gestures > 0 then
        viber.gesture(gestures[math.random(#gestures)])
      end
    end
    if viber.interacted("e") then
      local banter = viber.prop("banter")
      if #banter > 0 then
        st.i = ((st.i or 0) % #banter) + 1
        viber.toast(banter[st.i])
      end
      viber.gesture("talk")
    end
    return
  end
  local radius = viber.prop("radius")
  if radius <= 0 then return end -- balcão: não passeia
  if st.target == nil then
    local tx, tz = viber.wander_target(radius)
    st.target = { tx, tz }
  end
  local td = math.sqrt((st.target[1] - x) ^ 2 + (st.target[2] - z) ^ 2)
  -- Chegou: o limiar acompanha o raio (numa sala de 1.6 m, 0.6 m era meio passeio).
  if td < math.min(0.6, radius * 0.25) then
    st.target = nil
    st.stuck, st.last_td = 0, nil
    return
  end
  -- anti-stuck: td sem diminuir = preso num collider; novo destino (mais cedo
  -- em salas pequenas, onde o NPC encosta mais vezes a móveis/paredes).
  if st.last_td ~= nil and td >= st.last_td then
    st.stuck = (st.stuck or 0) + dt
    if st.stuck > math.min(6, 1 + radius) then
      local tx, tz = viber.wander_target(radius)
      st.target = { tx, tz }
      st.stuck, st.last_td = 0, nil
      return
    end
  else
    st.stuck = 0
  end
  st.last_td = td
  viber.move_towards(st.target[1], st.target[2], viber.prop("speed"))
end
