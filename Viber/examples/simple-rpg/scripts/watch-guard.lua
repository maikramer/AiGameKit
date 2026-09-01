-- watch-guard.lua: prompt de interação + efeito (portado do TS; colheita pesada chega
-- com o Destructible/Inventory da Fase 3).
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Falar com o guarda", "e", 3.5)
end

function on_update(dt)
  if viber.interacted("e") and not st.done then
    st.done = true
    viber.add_xp(5)
    viber.toast("“Mantenha os olhos na estrada, viajante.” (+5 XP)")
  end
end
