-- qa-commuter.lua: vaivém entre dois pontos SEPARADOS POR UMA PAREDE.
--
-- Não sabe que a parede existe: pede `move_towards` ao ponto do outro lado,
-- exactamente como os NPCs do simple-rpg. Quem tem de a contornar é a
-- navegação. Com `VIBER_NAV=0` esta entidade encosta-se à parede e fica lá.
local SPEED = 2.2
local A = { -30.0, 0.0 }
local B = { 30.0, 0.0 }

function on_update(dt)
  local st = viber.state()
  if not st.ready then
    st.ready = true
    st.leg = 1
  end
  local x, y, z = viber.position()
  local target = st.leg == 1 and B or A
  if math.sqrt((target[1] - x) ^ 2 + (target[2] - z) ^ 2) < 1.5 then
    st.leg = st.leg == 1 and 2 or 1
    target = st.leg == 1 and B or A
  end
  viber.move_towards(target[1], target[2], SPEED)
end
