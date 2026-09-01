-- chest.lua: baú de uma abertura só — loot real no vault/inventário
-- (ouro + poção) via hooks da economia.
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Abrir o baú", "e", 2.8)
end
function on_update(dt)
  if viber.interacted("e") and not st.opened then
    st.opened = true
    viber.add_xp(30)
    viber.vault_add("gold", 25)
    viber.item_add("potion", 1)
    viber.toast("O baú range e cede... +30 XP, +25 ouro, +1 poção")
  elseif viber.interacted("e") then
    viber.toast("O baú está vazio.")
  end
end
