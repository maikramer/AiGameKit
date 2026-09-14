-- crystal.lua: script colado ao <Prototype id="crystal"> — cada instância
-- spawnada nasce com este comportamento (luau_on_add corre no spawn).
-- [E] recolhe: soma em viber.game().score (estado de JOGO, partilhado e
-- persistente) e despawna com uma fanfarra pequena.

function on_update(dt)
  local st = viber.state() -- POR ENTIDADE
  if not st.ready then
    st.ready = true
    viber.set_interaction("Recolher cristal", "e", 3.0)
    local x, y, z = viber.position()
    st.base_y = y
  end
  -- flutuação suave em torno do Y de spawn (timers não — é animação por frame).
  -- `set_position` é o caminho LEGADO de posição: aqui é deliberado — o
  -- cristal não tem collider nem locomoção de IA, e o `move_towards` moderno
  -- assenta o Y no terreno, brigando com a flutuação.
  st.t = (st.t or math.random() * 6.28) + dt
  local x, _, z = viber.position()
  viber.set_position(x, st.base_y + 0.25 + math.sin(st.t * 2.0) * 0.15, z)
  if viber.interacted("e") and not st.done then
    st.done = true
    local g = viber.game()
    g.score = (g.score or 0) + 1
    viber.sound("loot")
    viber.toast("Cristal " .. g.score .. " / " .. (g.total or "?"))
    viber.save() -- persistência: o mundo retoma daqui no próximo arranque
    viber.despawn_self()
  end
end
