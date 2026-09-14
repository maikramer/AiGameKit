-- qa-hunter.lua: o MESMO trajecto do qa-commuter, mas sob `enemies/`.
--
-- É só isso que muda: `combat::is_hostile_script` lê o caminho, a engine
-- atribui `NavProfile::Wild`, e o custo extra de andar fora da estrada deixa de
-- se aplicar. Lado a lado com o commuter, mede-se a preferência por estradas
-- com o resto tudo igual — mesma velocidade, mesmos pontos, mesma parede.
local SPEED = 2.2
local A = { -30.0, -3.0 }
local B = { 30.0, -3.0 }

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
