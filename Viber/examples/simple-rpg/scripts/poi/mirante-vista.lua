local rules = viber.load("lib/rules.lua")
-- poi/mirante-vista.lua: objeto místico (portado do TS) — prompt quando perto, leitura dá
-- XP uma única vez (a marca vive no save — state() renasce vazio no load).
local once = viber.load("lib/once.lua")
function on_update(dt)
  local st = viber.state() -- POR ENTIDADE (no top-level era partilhado)
  if not st.ready then
    st.ready = true
    st.read = once.taken()
    viber.set_interaction("Contemplar a vista", "e", 4.0)
  end
  if viber.interacted("e") and not st.read then
    st.read = true
    once.mark()
    rules.add_xp(20)
    viber.toast("Do mirante, o vale inteiro se abre diante de ti (+20 XP).")
  end
end
