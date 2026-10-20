local rules = viber.load("lib/rules.lua")
-- poi/watch-tome.lua: objeto místico (portado do TS) — prompt quando perto, leitura dá
-- XP uma única vez (a marca vive no save — state() renasce vazio no load).
local once = viber.load("lib/once.lua")
function on_update(dt)
  local st = viber.state() -- POR ENTIDADE (no top-level era partilhado)
  if not st.ready then
    st.ready = true
    st.read = once.taken()
    viber.set_interaction("Ler o tomo", "e", 3.0)
  end
  if viber.interacted("e") and not st.read then
    st.read = true
    once.mark()
    rules.add_xp(25)
    viber.toast("“Páginas velhas sussurram: a torre vigia o norte.” (+25 XP)")
  end
end
