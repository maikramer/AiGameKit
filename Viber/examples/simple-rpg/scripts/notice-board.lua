-- notice-board.lua: prompt de interação + efeito (portado do TS; colheita pesada chega
-- com o Destructible/Inventory da Fase 3).
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Ler o quadro", "e", 3.0)
end

function on_update(dt)
  if viber.interacted("e") and not st.done then
    st.done = true
    viber.add_xp(5)
    viber.toast("“Mercador procura escolta até as ruínas. Pergunte na forja.”")
  end
end
