local rules = viber.load("lib/rules.lua")
-- crystal-shrine.lua: prompt de interação + efeito (portado do TS; colheita pesada chega
-- com o Destructible/Inventory da Fase 3).
local once = viber.load("lib/once.lua")
function on_update(dt)
  local st = viber.state() -- POR ENTIDADE (no top-level era partilhado)
  if not st.ready then
    st.ready = true
    st.done = once.taken()
    viber.set_interaction("Orar ao santuário", "e", 3.0)
  end
  if viber.interacted("e") and not st.done then
    st.done = true
    once.mark()
    rules.add_xp(20)
    viber.toast("Uma luz fria percorre o cristal (+20 XP).")
  end
end
