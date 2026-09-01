-- crystal-shrine.lua: prompt de interação + efeito (portado do TS; colheita pesada chega
-- com o Destructible/Inventory da Fase 3).
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Orar ao santuário", "e", 3.0)
end

function on_update(dt)
  if viber.interacted("e") and not st.done then
    st.done = true
    viber.add_xp(20)
    viber.toast("Uma luz fria percorre o cristal (+20 XP).")
  end
end
