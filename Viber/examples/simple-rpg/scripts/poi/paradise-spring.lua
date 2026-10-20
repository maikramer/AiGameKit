local rules = viber.load("lib/rules.lua")
-- poi/paradise-spring.lua: objeto místico (portado do TS) — prompt quando perto, leitura dá
-- XP uma única vez (a marca vive no save — state() renasce vazio no load).
local once = viber.load("lib/once.lua")
function on_update(dt)
  local st = viber.state() -- POR ENTIDADE (no top-level era partilhado)
  if not st.ready then
    st.ready = true
    st.read = once.taken()
    viber.set_interaction("Banhar-se na fonte", "e", 3.5)
  end
  if viber.interacted("e") and not st.read then
    st.read = true
    once.mark()
    rules.heal_player(40)
    rules.add_xp(35)
    viber.toast("A água da fonte restaura o corpo e a mente (+40 HP, +35 XP).")
  end
end
