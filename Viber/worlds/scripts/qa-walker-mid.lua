-- qa-walker.lua: vaivém determinístico entre dois pontos, sempre à mesma
-- velocidade, mais um `face_player()` por frame — a combinação exacta que
-- produzia o "andar de lado" nos scripts do simple-rpg.
--
-- Existe para MEDIR: o mundo `worlds/qa-locomotion.xml` amostra a entidade
-- pelo bridge e compara o passo real com o rumo e com o yaw.
local SPEED = 2.2 -- a patrulha de um lobo
local REACH = 9.0

function on_update(dt)
  local st = viber.state()
  local hx, hz = viber.home()
  if not st.ready then
    st.ready = true
    st.leg = 1
  end
  local x, y, z = viber.position()
  local tx = hx + (st.leg == 1 and REACH or -REACH)
  if math.abs(tx - x) < 0.4 then
    st.leg = st.leg == 1 and 2 or 1
    tx = hx + (st.leg == 1 and REACH or -REACH)
  end
  viber.move_towards(tx, hz, SPEED)
  -- Pedido de olhar em conflito DIRECTO com a marcha, todos os frames: a
  -- marcha é ao longo de X, o olhar é para +Z. É a combinação que punha o
  -- NPC a andar de lado.
  viber.face_towards(hx, hz + 30)
end
