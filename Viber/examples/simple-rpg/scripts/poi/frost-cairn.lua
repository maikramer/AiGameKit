-- poi/frost-cairn.lua: objeto místico (portado do TS) — prompt quando perto, leitura dá
-- XP uma única vez.
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Ler o mojão gelado", "e", 3.0)
end

function on_update(dt)
  if viber.interacted("e") and not st.read then
    st.read = true
    viber.add_xp(60)
    viber.toast("“As pedras empilhadas apontam para o oeste.” (+60 XP)")
  end
end
