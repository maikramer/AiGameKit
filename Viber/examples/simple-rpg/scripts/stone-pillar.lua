-- stone-pillar.lua: prompt de interação + efeito (portado do TS; colheita pesada chega
-- com o Destructible/Inventory da Fase 3).
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Ler as runas", "e", 3.0)
end

function on_update(dt)
  if viber.interacted("e") and not st.done then
    st.done = true
    viber.add_xp(10)
    viber.toast("Runas antigas: o caminho do sul serpenteia junto a agua.")
  end
end
