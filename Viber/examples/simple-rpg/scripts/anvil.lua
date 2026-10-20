-- anvil.lua: prompt de interação + efeito (portado do TS; colheita pesada chega
-- com o Destructible/Inventory da Fase 3).
local once = viber.load("lib/once.lua")
function on_update(dt)
  local st = viber.state() -- POR ENTIDADE (no top-level era partilhado)
  if not st.ready then
    st.ready = true
    st.done = once.taken()
    viber.set_interaction("Inspecionar a bigorna", "e", 2.5)
  end
  if viber.interacted("e") and not st.done then
    st.done = true
    once.mark()
    viber.toast("A bigorna do ferreiro ainda está quente.")
  end
end
