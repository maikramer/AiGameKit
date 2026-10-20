local rules = viber.load("lib/rules.lua")
-- well.lua: beber restaura 25 HP (o mesmo descanso da fogueira). Antes era
-- um toast único e a vida não mexia.
function on_update(dt)
  local st = viber.state() -- POR ENTIDADE (no top-level era partilhado)
  if not st.ready then
    st.ready = true
    viber.set_interaction("Beber", "e", 3.0)
  end
  if viber.interacted("e") then
    rules.heal_player(25)
    viber.toast("A água fresca restaura o ânimo (+25 HP).")
  end
end
