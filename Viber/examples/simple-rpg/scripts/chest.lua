-- chest.lua: baú de uma abertura só (ouro/XP; inventário chega na Fase 3).
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Abrir o baú", "e", 2.8)
end
function on_update(dt)
  if viber.interacted("e") and not st.opened then
    st.opened = true
    viber.add_xp(30)
    viber.toast("O baú range e cede... +30 XP")
  elseif viber.interacted("e") then
    viber.toast("O baú está vazio.")
  end
end
