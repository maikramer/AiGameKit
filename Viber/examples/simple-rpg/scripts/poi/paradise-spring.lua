-- poi/paradise-spring.lua: objeto místico (portado do TS) — prompt quando perto, leitura dá
-- XP uma única vez.
local st = viber.state()
if not st.ready then
  st.ready = true
  viber.set_interaction("Banhar-se na fonte", "e", 3.5)
end

function on_update(dt)
  if viber.interacted("e") and not st.read then
    st.read = true
    viber.add_xp(35)
    viber.toast("A água da fonte restaura o corpo e a mente (+35 XP).")
  end
end
