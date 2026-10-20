local rules = viber.load("lib/rules.lua")
-- campfire.lua: sentar/descansar na fogueira da praça (cura + toast).
function on_update(dt)
  local st = viber.state() -- POR ENTIDADE (no top-level era partilhado)
  if not st.ready then
    st.ready = true
    viber.set_interaction("Descansar", "e", 2.5)
  end
  if viber.interacted("e") then
    rules.heal_player(25)
    viber.toast("O calor da fogueira restaura 25 de vida.")
  end
end
