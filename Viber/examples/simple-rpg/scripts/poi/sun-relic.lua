-- poi/sun-relic.lua: objeto místico (portado do TS) — prompt quando perto, leitura dá
-- XP uma única vez.
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Tocar a relíquia", "e", 3.0)
end

function on_update(dt)
  if viber.interacted("e") and not st.read then
    st.read = true
    viber.add_xp(30)
    viber.toast("A relíquia do sol aquece suas mãos (+30 XP).")
  end
end
